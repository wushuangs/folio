import asyncio
import contextlib
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .config import UPLOADS, embedding_signature, settings, public_models, resolve_model
from .db import Chunk, Conversation, Document, IndexJob, Message, Revision, Session, SyncBinding, init_db, sync_fts, uid
from .ingestion import enqueue, worker
from .documents import FORMATS, source_path, validate_file
from .ocr import capabilities as ocr_capabilities
from .providers import ProviderError, probe_connection, stream_answer
from .retrieval import diagnose, invalidate_cache, retrieve
from .answer_policy import validate_response
from .conversation_context import prompt_context, rewrite_query, select_history


@asynccontextmanager
async def lifespan(app):
    init_db()
    invalidate_cache()
    with Session() as session:
        session.execute(update(IndexJob).where(IndexJob.status=='running').values(status='queued'))
        session.execute(update(Conversation).where(Conversation.status=='generating').values(status='open'))
        session.execute(update(Message).where(Message.role=='user', Message.status=='pending').values(status='interrupted'))
        for doc in session.scalars(select(Document).where(Document.status.in_(['processing','queued']))):
            pending=session.scalar(select(IndexJob).where(IndexJob.document_id==doc.id,IndexJob.status=='queued'))
            doc.status='ready' if doc.chunks else 'queued' if pending else 'error'
            doc.indexing_status='queued' if pending else 'error'
            if not pending:
                doc.error='上次任务中断，原有索引已保留，请重新索引。'
        session.commit()
    stop=asyncio.Event()
    task=asyncio.create_task(worker(stop))
    yield
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app=FastAPI(title='Folio Knowledge API',lifespan=lifespan)
MAX_BODY=52*1024*1024


class BodyTooLarge(Exception):
    pass


class RequestGuard:
    def __init__(self,app):
        self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        if scope.get('method') not in {'GET','HEAD','OPTIONS'}:
            from urllib.parse import urlparse
            origin=headers.get(b'origin',b'').decode()
            if origin and urlparse(origin).hostname not in {'localhost','127.0.0.1','[::1]','::1'}:
                return await JSONResponse({'detail':'不允许跨站修改本地知识库。'},status_code=403)(scope,receive,send)
        if scope.get('method')=='POST' and scope['path']=='/api/documents':
            try:
                if int(headers.get(b'content-length',b'0'))>MAX_BODY:
                    raise BodyTooLarge()
            except ValueError:
                return await JSONResponse({'detail':'无效的请求长度。'},status_code=400)(scope,receive,send)
            except BodyTooLarge:
                return await JSONResponse({'detail':'上传内容不能超过 50 MB。'},status_code=413)(scope,receive,send)
            total=0
            async def limited_receive():
                nonlocal total
                message=await receive()
                total+=len(message.get('body',b''))
                if total>MAX_BODY:
                    raise BodyTooLarge()
                return message
            try:
                return await self.app(scope,limited_receive,send)
            except BodyTooLarge:
                return await JSONResponse({'detail':'上传内容不能超过 50 MB。'},status_code=413)(scope,receive,send)
        return await self.app(scope,receive,send)


app.add_middleware(RequestGuard)

from .quality import router as quality_router
app.include_router(quality_router)
from .suggestions import router as suggestions_router
app.include_router(suggestions_router)
from .publication import router as publication_router
app.include_router(publication_router)
from .chunk_edit import router as chunk_edit_router
app.include_router(chunk_edit_router)
from .local_sync import router as local_sync_router
app.include_router(local_sync_router)


def serialize_doc(doc):
    return {key:getattr(doc,key) for key in ['id','name','size','pages','chunks','status','progress','error','created_at','active_revision','indexing_status','parser_version','file_type','quality_report']} | {'vector_ready':bool(doc.embedding_signature and doc.embedding_signature==embedding_signature())}


def serialize_message(m):
    return {key:getattr(m,key) for key in ['id','role','content','citations','mode','elapsed_ms','status','retrieval_mode','warning']} | {'approved_knowledge':m.request_context.get('approved_knowledge'),'model':m.request_context.get('model')}


@app.get('/api/health')
def health():
    s=settings()
    models=public_models()
    return {'status':'ok','generation_ready':any(model['ready'] for model in models),'embedding_ready':bool(embedding_signature()),'embedding_model':s['embedding_model'] or None,'model':s['llm_model'] or None,'mode':'rag' if s['llm_key'] and s['llm_model'] else 'extractive','models':models,'ocr':ocr_capabilities(),'supported_formats':sorted(FORMATS),'database': 'postgresql' if str(Session.kw['bind'].url).startswith('postgresql') else 'sqlite'}


@app.get('/api/documents')
def documents(include_archived:bool=False):
    with Session() as session:
        query=select(Document).order_by(Document.created_at.desc())
        if not include_archived:
            query=query.where(Document.status!='archived')
        return [serialize_doc(d) for d in session.scalars(query)]


@app.get('/api/health/network')
async def network_health(service:Literal['embedding','generation']='embedding'):
    return await probe_connection(service)


@app.post('/api/documents',status_code=202)
async def upload(file:UploadFile):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in FORMATS:
        await file.close()
        raise HTTPException(400,'支持 PDF、TXT、Markdown、DOCX、CSV、XLSX 和 PNG/JPEG/WebP 图片。')
    docid=uid()
    path=UPLOADS/f'{docid}{suffix}'
    digest,size=hashlib.sha256(),0
    try:
        with path.open('wb') as output:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>50*1024*1024:
                    raise HTTPException(413,'文件不能超过 50 MB。')
                digest.update(chunk);output.write(chunk)
        try:
            await asyncio.to_thread(validate_file,path,suffix)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        with Session() as session:
            existing=session.scalar(select(Document).where(Document.sha256==digest.hexdigest()))
            if existing:
                path.unlink(missing_ok=True)
                if existing.status=='archived':
                    existing.status='ready' if existing.chunks else 'error';session.commit();sync_fts(session,existing.id);session.commit();invalidate_cache()
                return serialize_doc(existing)
            doc=Document(id=docid,name=Path(file.filename.replace('\\','/')).name[:250],sha256=digest.hexdigest(),size=size,file_type=suffix[1:])
            session.add(doc)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing=session.scalar(select(Document).where(Document.sha256==digest.hexdigest()))
                path.unlink(missing_ok=True)
                return serialize_doc(existing)
        enqueue(docid)
        with Session() as session:
            return serialize_doc(session.get(Document,docid))
    except Exception:
        # 已创建数据库记录后的任务错误保留原文件，便于重试。
        with Session() as session:
            if not session.get(Document,docid):
                path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


class ReindexRequest(BaseModel):
    mode:Literal['full','text']='full'


@app.post('/api/documents/{document_id}/reindex',status_code=202)
def reindex(document_id:str,body:ReindexRequest=ReindexRequest()):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or doc.status=='archived':
            raise HTTPException(404,'文档不存在。')
        if session.scalar(select(SyncBinding.relative_path).where(
            SyncBinding.document_id==document_id,SyncBinding.pending_document_id.is_not(None))):
            raise HTTPException(409,'本地目录中的新版本正在建立索引，请等待同步完成。')
    try:
        jobid=enqueue(document_id,body.mode)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    return {'status':'queued','job_id':jobid}


@app.get('/api/documents/{document_id}/revisions')
def revisions(document_id:str):
    with Session() as session:
        if not session.get(Document,document_id):
            raise HTTPException(404,'文档不存在。')
        return [{'revision':r.revision,'pages':r.pages,'chunks':r.chunks,'parser_version':r.parser_version,'has_vectors':bool(r.embedding_signature),'created_at':r.created_at} for r in session.scalars(select(Revision).where(Revision.document_id==document_id).order_by(Revision.revision.desc()))]


class RollbackRequest(BaseModel):
    revision:int=Field(ge=0)


@app.post('/api/documents/{document_id}/rollback')
def rollback(document_id:str,body:RollbackRequest):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or doc.status=='archived':
            raise HTTPException(404,'文档不存在。')
        if doc.indexing_status in ['queued','processing']:
            raise HTTPException(409,'请等待当前索引任务结束。')
        revision=session.scalar(select(Revision).where(Revision.document_id==doc.id,Revision.revision==body.revision))
        if not revision:
            raise HTTPException(404,'索引版本不存在。')
        doc.active_revision,doc.pages,doc.chunks=revision.revision,revision.pages,revision.chunks
        doc.parser_version,doc.embedding_signature=revision.parser_version,revision.embedding_signature
        doc.quality_report=revision.quality_report
        doc.status,doc.error,doc.indexing_status='ready','','idle'
        session.flush();sync_fts(session,doc.id);session.commit()
    invalidate_cache()
    return {'status':'ready','revision':body.revision}


@app.delete('/api/documents/{document_id}')
def archive(document_id:str):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc:
            raise HTTPException(404,'文档不存在。')
        if doc.indexing_status in ['queued','processing']:
            raise HTTPException(409,'请等待索引任务结束再归档。')
        doc.status='archived';session.flush();sync_fts(session,doc.id);session.commit()
    invalidate_cache()
    return {'status':'archived'}


@app.post('/api/documents/{document_id}/restore')
def restore(document_id:str):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc:
            raise HTTPException(404,'文档不存在。')
        doc.status='ready' if doc.chunks else 'error';session.flush();sync_fts(session,doc.id);session.commit()
    invalidate_cache()
    return {'status':doc.status}


@app.get('/api/documents/{document_id}/chunks')
def preview_chunks(document_id:str,page:int=0,offset:int=Query(default=0,ge=0)):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc:
            raise HTTPException(404,'文档不存在。')
        statement=select(Chunk).where(Chunk.document_id==doc.id,Chunk.revision==doc.active_revision)
        if page:
            statement=statement.where(Chunk.page==page)
        return [{'id':c.id,'page':c.page,'heading':c.heading,'kind':c.kind,'content':c.content,
                 'enabled':c.enabled,'revision':c.revision} for c in session.scalars(statement.order_by(Chunk.ordinal).offset(offset).limit(30))]


@app.get('/api/documents/{document_id}/file')
def original(document_id:str):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or not source_path(doc).exists():
            raise HTTPException(404,'文档不存在。')
        return FileResponse(source_path(doc),media_type='application/pdf' if doc.file_type=='pdf' else 'application/octet-stream',filename=doc.name,content_disposition_type='inline' if doc.file_type=='pdf' else 'attachment')


class RetrievalDiagnosisRequest(BaseModel):
    question:str=Field(min_length=1,max_length=500)
    document_ids:list[str]=Field(default_factory=list,max_length=100)
    retrieval:Literal['hybrid','keyword']='hybrid'


@app.post('/api/retrieval/diagnose')
async def retrieval_diagnosis(request:RetrievalDiagnosisRequest):
    question=request.question.strip()
    if not question:
        raise HTTPException(422,'请输入问题。')
    with Session() as session:
        if request.document_ids:
            found=set(session.scalars(select(Document.id).where(Document.id.in_(request.document_ids),Document.status=='ready')))
            if found!=set(request.document_ids):
                raise HTTPException(409,'部分文档不可查询，请刷新文档范围。')
    query=rewrite_query(question,[])
    from .publication import exact_approved_answer
    exact=await asyncio.to_thread(exact_approved_answer,question,request.document_ids)
    if exact:
        return {'question':question,'query':query,'answer_path':'published_exact',
                'retrieval_mode':'approved','warning':'',
                'published':exact['metadata'],
                'trace':{'corpus_chunks':0,'scope_document_ids':request.document_ids,
                         'lexical':[],'vector':[],'fusion':[],'timings_ms':{},
                         'vector_state':'skipped','approved':[],
                         'final':[{'id':source['id'],'chunk_id':source['chunk_id'],
                                   'document_id':source['document_id'],
                                   'document_name':source['document_name'],'page':source['page'],
                                   'heading':source['heading'],'kind':'approved_qa',
                                   'content':source['content'][:1200]}
                                  for source in exact['citations']]}}
    mode,warning,trace=await diagnose(query,request.document_ids,request.retrieval=='hybrid')
    return {'question':question,'query':query,'answer_path':'retrieval',
            'retrieval_mode':mode,'warning':warning,'published':None,'trace':trace}


@app.get('/api/conversations')
def conversations():
    with Session() as session:
        return [{'id':c.id,'title':c.title,'created_at':c.created_at,'document_ids':c.document_ids} for c in session.scalars(select(Conversation).where(Conversation.app_id.is_(None)).order_by(Conversation.created_at.desc()).limit(100))]


@app.get('/api/conversations/{conversation_id}/messages')
def messages(conversation_id:str):
    with Session() as session:
        conversation=session.get(Conversation,conversation_id)
        if not conversation or conversation.app_id is not None:
            raise HTTPException(404,'会话不存在。')
        return [serialize_message(m) for m in session.scalars(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at,Message.id))]


class Question(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    model_id:str|None=Field(default=None,max_length=64)
    conversation_id:str|None=None
    request_id:str|None=Field(default=None,max_length=64)
    document_ids:list[str]=Field(default_factory=list,max_length=100)
    retrieval:Literal['auto','keyword']='auto'
    answer_mode:Literal['auto','extractive']='auto'


def event(kind,data):
    return f'event: {kind}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'


def source_location(citation):
    return f'PDF 第 {citation["page"]} 页' if citation.get('file_type','pdf')=='pdf' else f'第 {citation["page"]} 节 / {citation.get("heading", "正文")}'


def finalize_answer(answer,citations):
    policy=validate_response(answer,citations)
    if policy:return policy
    answer=re.split(r"\n###\s*参考依据",answer,maxsplit=1)[0]
    valid={c['id'] for c in citations}
    used={int(n) for n in re.findall(r'\[(\d+)\]',answer) if int(n) in valid}
    if not used:
        return '### 资料不足\n\n未能生成带有效来源的回答。请补充具体产品、参数或故障现象，或查看检索原文。',[], 'no_evidence','模型未返回有效引用，已停止展示不可核对的结论。'
    invalid={int(n) for n in re.findall(r'\[(\d+)\]',answer) if int(n) not in valid}
    if invalid:
        return '### 引用校验未通过\n\n模型使用了不存在的来源编号，本次回答未采纳。请查看检索原文或重试。',[], 'no_evidence','存在无效来源编号，已拦截生成结果。'
    answer=re.sub(r'(?m)^### [^\n]+\n\s*(?=### |$)','',answer).strip()
    if not answer.startswith('#'):answer='### 结论\n\n'+answer
    references=[c for c in citations if c['id'] in used]
    appendix='\n\n### 参考依据\n'+ '\n'.join(f'- [{c["id"]}] {c["document_name"]} · {source_location(c)}' for c in references)
    return answer.strip()+appendix,references,'generated',''


@app.post('/api/chat')
async def chat(request:Question):
    return await run_chat(request,None)


async def run_chat(request:Question,app_id:str|None):
    question=request.question.strip()
    if not question:
        raise HTTPException(400,'请输入问题。')
    try:
        profile=resolve_model(request.model_id)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    with Session() as session:
        if request.request_id and session.scalar(select(Message.id).where(Message.request_id==request.request_id)):
            raise HTTPException(409,'该问题已提交，请刷新会话查看结果。')
        if request.document_ids:
            found=set(session.scalars(select(Document.id).where(Document.id.in_(request.document_ids),Document.status=='ready')))
            if found!=set(request.document_ids):
                raise HTTPException(409,'部分文档不可查询，请刷新文档范围。')
        history=[]
        if request.conversation_id:
            conversation=session.get(Conversation,request.conversation_id)
            if not conversation or conversation.app_id!=app_id:
                raise HTTPException(404,'会话不存在。')
            claimed=session.execute(update(Conversation).where(Conversation.id==conversation.id,Conversation.status!='generating').values(status='generating'))
            if not claimed.rowcount:
                raise HTTPException(409,'该会话仍在生成回答，请等待完成。')
            previous=list(session.scalars(select(Message).where(Message.conversation_id==conversation.id).order_by(Message.created_at.desc(),Message.id.desc()).limit(40)))[::-1]
            history=select_history(previous,request.document_ids)
        else:
            conversation=Conversation(title=question[:40],status='generating',app_id=app_id)
            session.add(conversation);session.flush()
        conversation.document_ids=list(dict.fromkeys(request.document_ids))
        cid=conversation.id
        user_message=Message(conversation_id=cid,request_id=request.request_id,role='user',content=question,status='pending',request_context={'document_ids':request.document_ids})
        session.add(user_message)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409,'该问题已提交，请刷新会话查看结果。')
        user_message_id=user_message.id

    async def generate():
        start=time.monotonic();answer='';citations=[];mode='';retrieval_mode='';warning='';status='complete';message_id=uid();saved=False;approved_metadata=None
        try:
            yield event('start',{'conversation_id':cid})
            query=rewrite_query(question,history)
            from .publication import exact_approved_answer
            exact = await asyncio.to_thread(exact_approved_answer,question,request.document_ids) if request.answer_mode!='extractive' else None
            if exact:
                citations=exact['citations']
                approved_metadata=exact['metadata']
                mode,retrieval_mode='approved','approved'
            else:
                citations,retrieval_mode,warning=await retrieve(query,request.document_ids,allow_remote=request.retrieval!='keyword' and request.answer_mode!='extractive',include_approved=request.answer_mode!='extractive')
                mode='generated' if (profile['api_key'] or profile.get('local')) and profile['model'] and request.answer_mode!='extractive' else 'extractive'
            yield event('sources',{'citations':citations,'mode':mode,'retrieval_mode':retrieval_mode,'warning':warning,'query':query,'approved_knowledge':approved_metadata,'model_id':profile['id'],'model':profile['model']})
            if exact:
                # 完全匹配时直接返回审核版本，保留原始引用编号与内容，不再调用模型改写。
                answer=exact['answer']
                yield event('delta',{'text':answer})
            elif not citations:
                answer='### 资料不足\n\n当前知识库没有找到足够相关的依据。请补充具体参数名称、型号或故障现象。'
                mode='no_evidence'
                yield event('delta',{'text':answer})
            elif mode=='extractive':
                answer='### 检索结果\n\n以下为本地检索得到的原文，请结合页码核对。\n\n'
                for c in citations[:3]:
                    quote='\n'.join('> '+line for line in c['matched_content'].splitlines())
                    answer+=f'**[{c["id"]}] {c["heading"]} · {source_location(c)}**\n\n{quote}\n\n'
                yield event('delta',{'text':answer})
            else:
                context=prompt_context(citations)
                async for delta in (stream_answer(question,context,history,model_id=request.model_id) if request.model_id else stream_answer(question,context,history)):
                    answer+=delta
                    if len(answer)>30000:raise ProviderError('回答超出长度限制，请缩小问题范围。')
                if not answer.strip():
                    raise ProviderError('模型返回了空回答，请重试。')
                answer,citations,mode,validation_warning=finalize_answer(answer,citations)
                warning=' '.join(x for x in [warning,validation_warning] if x)
                yield event('replace',{'content':answer,'citations':citations,'mode':mode,'warning':warning})
            elapsed=int((time.monotonic()-start)*1000)
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content=answer,citations=citations,mode=mode,elapsed_ms=elapsed,status=status,retrieval_mode=retrieval_mode,warning=warning,request_context={'question':question,'document_ids':request.document_ids,'history':history,'approved_knowledge':approved_metadata,'model_id':profile['id'],'model':profile['model']}))
                session.get(Message,user_message_id).status='complete'
                session.commit()
            saved=True
            yield event('done',{'message_id':message_id,'elapsed_ms':elapsed,'mode':mode,'warning':warning,'retrieval_mode':retrieval_mode,'approved_knowledge':approved_metadata,'model_id':profile['id'],'model':profile['model']})
        except asyncio.CancelledError:
            if saved:
                raise
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content='生成已取消，可重新提问。',citations=[],mode='cancelled',status='cancelled',retrieval_mode=retrieval_mode,warning=warning,request_context={'question':question,'document_ids':request.document_ids,'history':history,'approved_knowledge':approved_metadata,'model_id':profile['id'],'model':profile['model']}))
                session.get(Message,user_message_id).status='cancelled';session.commit()
            raise
        except Exception as exc:
            error=str(exc) if isinstance(exc,ProviderError) else '处理失败，请稍后重试。'
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content=error,citations=[],mode='error',status='error',retrieval_mode=retrieval_mode,warning=warning,request_context={'question':question,'document_ids':request.document_ids,'history':history,'approved_knowledge':approved_metadata,'model_id':profile['id'],'model':profile['model']}))
                session.get(Message,user_message_id).status='error';session.commit()
            yield event('error',{'message':error,'message_id':message_id})
        finally:
            with Session() as session:
                session.execute(update(Conversation).where(Conversation.id==cid).values(status='open'));session.commit()
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


from .integrations import router as integrations_router
app.include_router(integrations_router)
