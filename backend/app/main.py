import asyncio
import contextlib
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .config import UPLOADS, embedding_signature, settings
from .db import Chunk, Conversation, Document, IndexJob, Message, Revision, Session, init_db, sync_fts, uid
from .ingestion import enqueue, worker
from .providers import ProviderError, probe_connection, stream_answer
from .retrieval import invalidate_cache, retrieve


@asynccontextmanager
async def lifespan(app):
    init_db()
    invalidate_cache()
    with Session() as session:
        session.execute(update(IndexJob).where(IndexJob.status=='running').values(status='queued'))
        session.execute(update(Conversation).where(Conversation.status=='generating').values(status='open'))
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


def serialize_doc(doc):
    return {key:getattr(doc,key) for key in ['id','name','size','pages','chunks','status','progress','error','created_at','active_revision','indexing_status','parser_version']} | {'vector_ready':bool(doc.embedding_signature and doc.embedding_signature==embedding_signature())}


def serialize_message(m):
    return {key:getattr(m,key) for key in ['id','role','content','citations','mode','elapsed_ms','status','retrieval_mode','warning']}


@app.get('/api/health')
def health():
    s=settings()
    return {'status':'ok','generation_ready':bool(s['llm_key'] and s['llm_model']),'embedding_ready':bool(embedding_signature()),'model':s['llm_model'] or None,'mode':'rag' if s['llm_key'] and s['llm_model'] else 'extractive','database': 'postgresql' if str(Session.kw['bind'].url).startswith('postgresql') else 'sqlite'}


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
    if not (file.filename or '').lower().endswith('.pdf'):
        await file.close()
        raise HTTPException(400,'仅支持 PDF 文件。')
    docid=uid()
    path=UPLOADS/f'{docid}.pdf'
    digest,size=hashlib.sha256(),0
    try:
        with path.open('wb') as output:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>50*1024*1024:
                    raise HTTPException(413,'PDF 不能超过 50 MB。')
                digest.update(chunk);output.write(chunk)
        with path.open('rb') as source:
            if b'%PDF-' not in source.read(1024):
                raise HTTPException(400,'文件不是有效的 PDF。')
        with Session() as session:
            existing=session.scalar(select(Document).where(Document.sha256==digest.hexdigest()))
            if existing:
                path.unlink(missing_ok=True)
                if existing.status=='archived':
                    existing.status='ready' if existing.chunks else 'error';session.commit();sync_fts(session,existing.id);session.commit();invalidate_cache()
                return serialize_doc(existing)
            doc=Document(id=docid,name=Path(file.filename.replace('\\','/')).name[:250],sha256=digest.hexdigest(),size=size)
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
def preview_chunks(document_id:str,page:int=0):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc:
            raise HTTPException(404,'文档不存在。')
        statement=select(Chunk).where(Chunk.document_id==doc.id,Chunk.revision==doc.active_revision)
        if page:
            statement=statement.where(Chunk.page==page)
        return [{'id':c.id,'page':c.page,'heading':c.heading,'kind':c.kind,'content':c.content} for c in session.scalars(statement.order_by(Chunk.ordinal).limit(30))]


@app.get('/api/documents/{document_id}/file')
def original(document_id:str):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or not (UPLOADS/f'{document_id}.pdf').exists():
            raise HTTPException(404,'文档不存在。')
        return FileResponse(UPLOADS/f'{document_id}.pdf',media_type='application/pdf',filename=doc.name,content_disposition_type='inline')


@app.get('/api/conversations')
def conversations():
    with Session() as session:
        return [{'id':c.id,'title':c.title,'created_at':c.created_at,'document_ids':c.document_ids} for c in session.scalars(select(Conversation).order_by(Conversation.created_at.desc()).limit(100))]


@app.get('/api/conversations/{conversation_id}/messages')
def messages(conversation_id:str):
    with Session() as session:
        if not session.get(Conversation,conversation_id):
            raise HTTPException(404,'会话不存在。')
        return [serialize_message(m) for m in session.scalars(select(Message).where(Message.conversation_id==conversation_id).order_by(Message.created_at,Message.id))]


class Question(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    conversation_id:str|None=None
    document_ids:list[str]=Field(default_factory=list,max_length=100)
    retrieval:Literal['auto','keyword']='auto'
    answer_mode:Literal['auto','extractive']='auto'


def event(kind,data):
    return f'event: {kind}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'


def rewrite_query(question,history):
    if len(question)>28 or not re.search(r'它|这个|那个|还有|具体|继续|呢|上述|那默认|那范围',question):
        return question
    previous=next((m['content'] for m in reversed(history) if m['role']=='user'),'')
    return (previous[:240]+' '+question).strip()


def finalize_answer(answer,citations):
    valid={c['id'] for c in citations}
    used={int(n) for n in re.findall(r'\[(\d+)\]',answer) if int(n) in valid}
    if not used:
        return '### 资料不足\n\n未能生成带有效来源的回答。请补充具体产品、参数或故障现象，或查看检索原文。',[], 'no_evidence','模型未返回有效引用，已停止展示不可核对的结论。'
    invalid={int(n) for n in re.findall(r'\[(\d+)\]',answer) if int(n) not in valid}
    if invalid:
        return '### 引用校验未通过\n\n模型使用了不存在的来源编号，本次回答未采纳。请查看检索原文或重试。',[], 'no_evidence','存在无效来源编号，已拦截生成结果。'
    references=[c for c in citations if c['id'] in used]
    appendix='\n\n### 参考依据\n'+ '\n'.join(f'- [{c["id"]}] {c["document_name"]} · PDF 第 {c["page"]} 页' for c in references)
    return answer.strip()+appendix,references,'generated',''


@app.post('/api/chat')
async def chat(request:Question):
    question=request.question.strip()
    if not question:
        raise HTTPException(400,'请输入问题。')
    with Session() as session:
        if request.document_ids:
            found=set(session.scalars(select(Document.id).where(Document.id.in_(request.document_ids),Document.status=='ready')))
            if found!=set(request.document_ids):
                raise HTTPException(409,'部分文档不可查询，请刷新文档范围。')
        history=[]
        if request.conversation_id:
            conversation=session.get(Conversation,request.conversation_id)
            if not conversation:
                raise HTTPException(404,'会话不存在。')
            claimed=session.execute(update(Conversation).where(Conversation.id==conversation.id,Conversation.status!='generating').values(status='generating'))
            if not claimed.rowcount:
                raise HTTPException(409,'该会话仍在生成回答，请等待完成。')
            previous=list(session.scalars(select(Message).where(Message.conversation_id==conversation.id,Message.status=='complete').order_by(Message.created_at.desc()).limit(6)))[::-1]
            history=[]
            pending=None
            for message in previous:
                if message.role=='user':
                    pending=message
                elif message.role=='assistant' and pending:
                    history.extend([{'role':'user','content':pending.content[:1800]},{'role':'assistant','content':message.content[:1800]}])
                    pending=None
        else:
            conversation=Conversation(title=question[:40],status='generating')
            session.add(conversation);session.flush()
        conversation.document_ids=list(dict.fromkeys(request.document_ids))
        cid=conversation.id
        session.add(Message(conversation_id=cid,role='user',content=question))
        session.commit()

    async def generate():
        start=time.monotonic();answer='';citations=[];mode='';retrieval_mode='';warning='';status='complete';message_id=uid();saved=False
        try:
            yield event('start',{'conversation_id':cid})
            query=rewrite_query(question,history)
            citations,retrieval_mode,warning=await retrieve(query,request.document_ids,allow_remote=request.retrieval!='keyword' and request.answer_mode!='extractive')
            s=settings();mode='generated' if s['llm_key'] and s['llm_model'] and request.answer_mode!='extractive' else 'extractive'
            yield event('sources',{'citations':citations,'mode':mode,'retrieval_mode':retrieval_mode,'warning':warning,'query':query})
            if not citations:
                answer='### 资料不足\n\n当前知识库没有找到足够相关的依据。请补充具体参数名称、型号或故障现象。'
                mode='no_evidence'
                yield event('delta',{'text':answer})
            elif mode=='extractive':
                answer='### 检索结果\n\n以下为本地检索得到的原文，请结合页码核对。\n\n'
                for c in citations[:3]:
                    quote='\n'.join('> '+line for line in c['matched_content'].splitlines())
                    answer+=f'**[{c["id"]}] {c["heading"]} · PDF 第 {c["page"]} 页**\n\n{quote}\n\n'
                yield event('delta',{'text':answer})
            else:
                context='\n\n'.join(f'[{c["id"]}] {c["document_name"]}，PDF 第 {c["page"]} 页，{c["heading"]}\n{c["content"]}' for c in citations)
                async for delta in stream_answer(question,context,history):
                    answer+=delta
                    yield event('delta',{'text':delta})
                if not answer.strip():
                    raise ProviderError('模型返回了空回答，请重试。')
                answer,citations,mode,validation_warning=finalize_answer(answer,citations)
                warning=' '.join(x for x in [warning,validation_warning] if x)
                yield event('replace',{'content':answer,'citations':citations,'mode':mode,'warning':warning})
            elapsed=int((time.monotonic()-start)*1000)
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content=answer,citations=citations,mode=mode,elapsed_ms=elapsed,status=status,retrieval_mode=retrieval_mode,warning=warning))
                session.commit()
            saved=True
            yield event('done',{'message_id':message_id,'elapsed_ms':elapsed,'mode':mode,'warning':warning,'retrieval_mode':retrieval_mode})
        except asyncio.CancelledError:
            if saved:
                raise
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content='生成已取消，可重新提问。',citations=[],mode='cancelled',status='cancelled',retrieval_mode=retrieval_mode,warning=warning));session.commit()
            raise
        except Exception as exc:
            error=str(exc) if isinstance(exc,ProviderError) else '处理失败，请稍后重试。'
            with Session() as session:
                session.add(Message(id=message_id,conversation_id=cid,role='assistant',content=error,citations=[],mode='error',status='error',retrieval_mode=retrieval_mode,warning=warning));session.commit()
            yield event('error',{'message':error,'message_id':message_id})
        finally:
            with Session() as session:
                session.execute(update(Conversation).where(Conversation.id==cid).values(status='open'));session.commit()
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
