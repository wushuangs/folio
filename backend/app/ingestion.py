import asyncio
import hashlib

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .config import UPLOADS, embedding_signature
from .db import Chunk, Document, EmbeddingCache, IndexJob, Revision, Session, now, sync_fts
from .parsing import PARSER_VERSION, parse_pdf, split_page
from .providers import ProviderError, embed
from .text import content_terms


def _invalidate():
    from .retrieval import invalidate_cache
    invalidate_cache()


def enqueue(document_id, mode='full'):
    with Session() as session:
        doc = session.get(Document,document_id)
        if not doc or doc.status=='archived':
            raise ValueError('文档不存在或已归档。')
        claimed = session.execute(update(Document).where(Document.id==document_id,Document.indexing_status.not_in(['queued','processing'])).values(indexing_status='queued',progress=0,error=''))
        if not claimed.rowcount:
            raise ValueError('文档正在处理中。')
        job = IndexJob(document_id=document_id,mode=mode)
        session.add(job)
        session.commit()
        return job.id


async def ingest(document_id: str, mode='full'):
    with Session() as session:
        doc=session.get(Document,document_id)
        if not doc or doc.status=='archived':
            return False
        doc.indexing_status,doc.error,doc.progress='processing','',2
        session.commit()
    try:
        def progress(current,total):
            if current%10==0 or current==total:
                with Session() as session:
                    doc=session.get(Document,document_id)
                    if doc:
                        doc.progress=5+int(current/total*55)
                        session.commit()
        pages,parsed=await asyncio.to_thread(parse_pdf,UPLOADS/f'{document_id}.pdf',progress)
        signature=embedding_signature() if mode=='full' else ''
        vectors=[]
        warning=''
        if signature:
            inputs=[c['heading']+'\n'+c['content'] for c in parsed]
            keys=[hashlib.sha256((signature+'\n'+value).encode()).hexdigest() for value in inputs]
            with Session() as session:
                cached={row.key:row.vector for row in session.scalars(select(EmbeddingCache).where(EmbeddingCache.key.in_(keys)))}
            missing=[i for i,key in enumerate(keys) if key not in cached]
            for start in range(0,len(missing),16):
                indices=missing[start:start+16]
                batch=await embed([inputs[i] for i in indices])
                with Session() as session:
                    for i,vector in zip(indices,batch):
                        cached[keys[i]]=vector
                        if not session.get(EmbeddingCache,keys[i]):
                            session.add(EmbeddingCache(key=keys[i],vector=vector))
                    doc=session.get(Document,document_id)
                    doc.progress=60+int(min(start+16,len(missing))/max(1,len(missing))*35)
                    session.commit()
            vectors=[cached[key] for key in keys]
            if any(len(v)!=len(vectors[0]) for v in vectors):
                raise ProviderError('不同批次向量维度不一致，旧索引已保留。')
        elif mode=='full':
            warning='未配置向量模型，已建立关键词索引。'
        with Session() as session:
            doc=session.get(Document,document_id)
            if not doc or doc.status=='archived':
                return False
            # 新版本和指针在同一事务中提交，失败不覆盖旧版本。
            if doc.chunks and not session.scalar(select(Revision).where(Revision.document_id==doc.id,Revision.revision==doc.active_revision)):
                session.add(Revision(document_id=doc.id,revision=doc.active_revision,pages=doc.pages,chunks=doc.chunks,embedding_signature=doc.embedding_signature,parser_version=doc.parser_version))
            revision=(session.scalar(select(func.max(Chunk.revision)).where(Chunk.document_id==doc.id)) or 0)+1
            for i,chunk in enumerate(parsed):
                session.add(Chunk(document_id=doc.id,revision=revision,**chunk,search_terms=content_terms(chunk['heading']+' '+chunk['content']),embedding=vectors[i] if vectors else None))
            session.add(Revision(document_id=doc.id,revision=revision,pages=pages,chunks=len(parsed),embedding_signature=signature,parser_version=PARSER_VERSION))
            doc.active_revision,doc.pages,doc.chunks=revision,pages,len(parsed)
            doc.status,doc.indexing_status,doc.progress='ready','idle',100
            doc.error,doc.embedding_signature,doc.parser_version=warning,signature,PARSER_VERSION
            session.flush()
            sync_fts(session,doc.id)
            session.commit()
        _invalidate()
        return True
    except asyncio.CancelledError:
        with Session() as session:
            doc=session.get(Document,document_id)
            if doc:
                doc.indexing_status='queued'
                session.commit()
        raise
    except Exception as exc:
        with Session() as session:
            doc=session.get(Document,document_id)
            if doc:
                doc.indexing_status='error'
                doc.status='ready' if doc.chunks else 'error'
                reason=str(exc) if isinstance(exc,(ValueError,ProviderError)) else '解析或入库失败，请检查文件。'
                doc.error=reason+(' 旧索引仍可查询。' if doc.chunks else ' 可修正后重新索引。')
                session.commit()
        return False


async def worker(stop):
    while not stop.is_set():
        with Session() as session:
            job=session.scalar(select(IndexJob).where(IndexJob.status=='queued').order_by(IndexJob.created_at))
            if job:
                claimed=session.execute(update(IndexJob).where(IndexJob.id==job.id,IndexJob.status=='queued').values(status='running'))
                session.commit()
                if not claimed.rowcount:
                    continue
                job_id,doc_id,mode=job.id,job.document_id,job.mode
            else:
                job_id=None
        if job_id:
            try:
                success=await ingest(doc_id,mode)
                with Session() as session:
                    job=session.get(IndexJob,job_id)
                    doc=session.get(Document,doc_id)
                    job.status='succeeded' if success else 'failed'
                    job.error=doc.error if doc else '文档不存在。'
                    job.finished_at=now()
                    session.commit()
            except asyncio.CancelledError:
                with Session() as session:
                    job=session.get(IndexJob,job_id)
                    if job:
                        job.status='queued'
                        session.commit()
                raise
        else:
            try:
                await asyncio.wait_for(stop.wait(),timeout=1)
            except TimeoutError:
                pass
