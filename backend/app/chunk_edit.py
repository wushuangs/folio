import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from .config import embedding_signature
from .db import Chunk, ChunkEdit, Document, Revision, Session, SyncBinding, sync_fts, uid
from .providers import ProviderError, embed
from .retrieval import invalidate_cache
from .text import content_terms

router=APIRouter(prefix='/api')


class ChunkEditInput(BaseModel):
    expected_revision:int=Field(ge=0)
    content:str=Field(max_length=4000)
    enabled:bool
    reason:str=Field(min_length=1,max_length=500)
    rebuild_vectors:bool=True


@router.patch('/documents/{document_id}/chunks/{chunk_id}')
async def edit_chunk(document_id:str,chunk_id:str,body:ChunkEditInput):
    content=body.content.strip()
    if body.enabled and not content:
        raise HTTPException(422,'启用的片段不能没有内容。')
    if not body.reason.strip():
        raise HTTPException(422,'请填写修正原因。')
    with Session() as session:
        doc=session.get(Document,document_id)
        old=session.get(Chunk,chunk_id)
        if not doc or doc.status!='ready' or not old or old.document_id!=doc.id or old.revision!=doc.active_revision:
            raise HTTPException(404,'当前版本的片段不存在。')
        if doc.active_revision!=body.expected_revision or doc.indexing_status in {'queued','processing'}:
            raise HTTPException(409,'文档索引已变化或正在重建，请刷新后重试。')
        if session.scalar(select(SyncBinding.relative_path).where(
            SyncBinding.document_id==document_id,SyncBinding.pending_document_id.is_not(None))):
            raise HTTPException(409,'本地目录中的新版本正在建立索引，请等待同步完成。')
        if old.content==content and old.enabled==body.enabled:
            raise HTTPException(409,'片段内容和启用状态没有变化。')
        old_heading=old.heading
        old_embedding=old.embedding
        previous_signature=doc.embedding_signature
    new_signature=previous_signature if body.rebuild_vectors else ''
    new_embedding=old_embedding
    if new_signature and body.enabled and old.content!=content:
        if embedding_signature()!=new_signature:
            raise HTTPException(409,'当前向量模型与索引不一致。请先重建向量，或取消“保留向量索引”后保存关键词版本。')
        try:
            new_embedding=(await embed([old_heading+'\n'+content]))[0]
        except ProviderError as exc:
            raise HTTPException(502,str(exc)+' 旧索引已保留；可取消“保留向量索引”后重试。') from exc
        if old_embedding is not None and len(new_embedding)!=len(old_embedding):
            raise HTTPException(502,'向量维度与旧索引不一致，修正未保存。')
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or doc.status!='ready':
            raise HTTPException(409,'文档状态已变化，请刷新。')
        chunks=list(session.scalars(select(Chunk).where(
            Chunk.document_id==document_id,Chunk.revision==body.expected_revision
        ).order_by(Chunk.ordinal)))
        target=next((chunk for chunk in chunks if chunk.id==chunk_id),None)
        if not target or doc.active_revision!=body.expected_revision or doc.indexing_status in {'queued','processing'}:
            raise HTTPException(409,'片段或索引版本已变化，请刷新后重试。')
        if session.scalar(select(SyncBinding.relative_path).where(
            SyncBinding.document_id==document_id,SyncBinding.pending_document_id.is_not(None))):
            raise HTTPException(409,'本地目录中的新版本正在建立索引，请等待同步完成。')
        highest=session.scalar(select(func.max(Revision.revision)).where(Revision.document_id==document_id)) or 0
        revision=max(highest,body.expected_revision)+1
        claimed=session.execute(update(Document).where(
            Document.id==document_id,Document.active_revision==body.expected_revision,
            Document.indexing_status.not_in(['queued','processing'])
        ).values(active_revision=revision))
        if not claimed.rowcount:
            raise HTTPException(409,'索引版本已变化，请刷新后重试。')
        replacement_id=''
        for chunk in chunks:
            changed=chunk.id==chunk_id
            value=content if changed else chunk.content
            enabled=body.enabled if changed else chunk.enabled
            new_id=uid()
            if changed:
                replacement_id=new_id
            session.add(Chunk(
                id=new_id,document_id=document_id,revision=revision,page=chunk.page,
                ordinal=chunk.ordinal,heading=chunk.heading,content=value,
                kind=chunk.kind,context=value if changed else chunk.context,
                group_key=chunk.group_key,
                search_terms=content_terms(chunk.heading+' '+value) if changed else chunk.search_terms,
                embedding=(new_embedding if changed else chunk.embedding) if new_signature else None,
                enabled=enabled,
            ))
        report=dict(doc.quality_report or {})
        warnings=[warning for warning in report.get('warnings') or []
                  if not re.match(r'^\d+ 个位置(?:未提取到文字|文字较少)',warning)]
        note='包含人工修正片段，请对照原文件核对。'
        if note not in warnings:
            warnings.append(note)
        report['warnings']=warnings
        report['status']='warning'
        report['characters']=sum(len(content if chunk.id==chunk_id else chunk.content)
                                 for chunk in chunks if (body.enabled if chunk.id==chunk_id else chunk.enabled))
        report['chunks']=len(chunks)
        page_characters={str(page):0 for page in range(1,doc.pages+1)}
        for chunk in chunks:
            if (body.enabled if chunk.id==chunk_id else chunk.enabled):
                page_characters[str(chunk.page)]=page_characters.get(str(chunk.page),0)+len(
                    content if chunk.id==chunk_id else chunk.content)
        report['page_characters']=page_characters
        report['empty_pages']=[int(page) for page,size in page_characters.items() if size==0]
        report['sparse_pages']=[int(page) for page,size in page_characters.items() if 0<size<40]
        if report['empty_pages']:
            warnings.insert(0,f'{len(report["empty_pages"])} 个位置未提取到文字（可能为空白页、目录或未识别扫描页）')
        if report['sparse_pages']:
            warnings.insert(0,f'{len(report["sparse_pages"])} 个位置文字较少，请核对完整性')
        session.add(Revision(document_id=document_id,revision=revision,pages=doc.pages,
                             chunks=len(chunks),embedding_signature=new_signature,
                             parser_version=doc.parser_version,quality_report=report))
        session.add(ChunkEdit(document_id=document_id,old_chunk_id=chunk_id,new_chunk_id=replacement_id,
                              from_revision=body.expected_revision,to_revision=revision,
                              old_content=target.content,new_content=content,
                              old_enabled=target.enabled,new_enabled=body.enabled,reason=body.reason.strip()))
        doc.embedding_signature=new_signature
        doc.quality_report=report
        doc.error='' if new_signature or not previous_signature else '人工修正已保存为关键词索引；如需向量检索，请执行向量重建。'
        session.flush()
        sync_fts(session,document_id)
        session.commit()
    invalidate_cache()
    return {'revision':revision,'chunk_id':replacement_id,'vector_ready':bool(new_signature)}


@router.get('/documents/{document_id}/chunk-edits')
def chunk_edit_history(document_id:str):
    with Session() as session:
        if not session.get(Document,document_id):
            raise HTTPException(404,'文档不存在。')
        rows=session.scalars(select(ChunkEdit).where(ChunkEdit.document_id==document_id)
                             .order_by(ChunkEdit.created_at.desc()).limit(50))
        return [{'from_revision':row.from_revision,'to_revision':row.to_revision,
                 'page':session.get(Chunk,row.old_chunk_id).page if session.get(Chunk,row.old_chunk_id) else None,
                 'reason':row.reason,'old_enabled':row.old_enabled,'new_enabled':row.new_enabled,
                 'created_at':row.created_at.isoformat()} for row in rows]
