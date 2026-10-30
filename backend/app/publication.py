import re
import threading
from functools import wraps
from datetime import timezone
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import AnswerFeedback, Chunk, Document, PublishedKnowledge, PublicationEvent, Session, now

router = APIRouter(prefix='/api')
_publication_lock = threading.RLock()


def serialized_publication(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        # 当前单 worker 部署下串行审核事务，避免重复发布与 SQLite 写竞争。
        with _publication_lock:
            return function(*args, **kwargs)
    return guarded



def active_sources(session, sources):
    if not sources:
        return False
    for source in sources:
        doc = session.get(Document, source['document_id'])
        chunk = session.get(Chunk, source['chunk_id'])
        if not doc or doc.status != 'ready' or not chunk or chunk.document_id != doc.id or chunk.revision != doc.active_revision or source.get('revision') != doc.active_revision:
            return False
    return True


def serialize_knowledge(session, row):
    if not row:
        return None
    date = row.updated_at
    return {key: getattr(row,key) for key in ['id','question','answer','sources','revision','status','review_note']} | {
        'updated_at': (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).isoformat(),
        'retrieval_active': row.status == 'published' and active_sources(session,row.sources),
    }


def publication_sources(session, feedback, published=None):
    result = {}
    for name, values in [
        ('snapshot', feedback.snapshot.get('citations', [])),
        ('verification', feedback.verification.get('citations', [])),
        ('published', published.sources if published else []),
    ]:
        # 旧验证可能混入正式知识；整组隔离，不能靠自己的答案证明自己正确。
        if name == 'verification' and any(c.get('kind') == 'approved_qa' for c in values):
            result[name] = {'items': [], 'warning': '这次旧验证包含已发布问答，不能作为独立审核依据。请重新执行原文验证，或使用原回答/已发布版本的来源。'}
            continue
        items, seen = [], set()
        for citation in values:
            if citation.get('kind') == 'approved_qa' or not citation.get('chunk_id') or citation['chunk_id'] in seen:
                continue
            seen.add(citation['chunk_id'])
            chunk = session.get(Chunk, citation['chunk_id'])
            doc = session.get(Document, citation.get('document_id'))
            reason = ''
            if not doc or not chunk or chunk.document_id != doc.id:
                reason = '原文来源不存在'
            elif doc.status != 'ready':
                reason = '来源文档已归档或尚未就绪'
            elif chunk.revision != doc.active_revision or citation.get('revision') != doc.active_revision:
                reason = '索引版本已变化，请重新验证'
            items.append(dict(citation, is_current=not reason, source_issue=reason))
        result[name] = {'items': items, 'warning': ''}
    return result


class PublishInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=6000)
    source_set: str = Field(pattern='^(snapshot|verification|published)$')
    source_ids: list[str] = Field(min_length=1, max_length=6)
    review_note: str = Field(min_length=1, max_length=2000)
    confirmed: bool
    expected_revision: int = Field(default=0, ge=0)


@router.post('/feedback/{feedback_id}/publish')
@serialized_publication
def publish(feedback_id: str, body: PublishInput):
    if not body.confirmed or not all(x.strip() for x in [body.question,body.answer,body.review_note]):
        raise HTTPException(422,'请确认已人工核对答案与来源，并填写审核说明。')
    with Session() as session:
        feedback = session.get(AnswerFeedback,feedback_id)
        if not feedback:
            raise HTTPException(404,'反馈不存在。')
        existing_publication=session.scalar(select(PublishedKnowledge).where(PublishedKnowledge.feedback_id==feedback_id))
        options=publication_sources(session,feedback,existing_publication)[body.source_set]
        if options['warning']:
            raise HTTPException(409, options['warning'])
        candidates = {c['chunk_id']:c for c in options['items']}
        ids = list(dict.fromkeys(body.source_ids))
        if any(key not in candidates for key in ids):
            raise HTTPException(422,'请选择本次反馈原回答或验证结果中的原文来源，不可将已发布答案作为原文循环入库。')
        sources=[]
        for key in ids:
            old=candidates[key]
            chunk=session.get(Chunk,key)
            doc=session.get(Document,old['document_id'])
            if not chunk or not doc or chunk.document_id != doc.id or doc.status!='ready' or chunk.revision!=doc.active_revision or old.get('revision') != doc.active_revision:
                raise HTTPException(409,'来源版本已变化或文档已归档，请重新检索验证并选择最新原文。')
            sources.append({'id':old['id'],'chunk_id':chunk.id,'document_id':doc.id,'document_name':doc.name,
                            'page':chunk.page,'revision':chunk.revision,'heading':chunk.heading,'content':chunk.context or chunk.content})
        used={int(n) for n in re.findall(r'\[(\d+)\]',body.answer)}
        if used - {s['id'] for s in sources}:
            missing = sorted(used - {s['id'] for s in sources})
            raise HTTPException(422,'标准答案仍引用 '+ '、'.join(f'[{n}]' for n in missing) +'，但未勾选这些来源。请补选对应依据，或修改答案中的引用编号；不同来源组的编号不能混用。')
        from .text import normalize
        source_scope={source['document_id'] for source in sources}
        for existing in session.scalars(select(PublishedKnowledge).where(PublishedKnowledge.status=='published',PublishedKnowledge.feedback_id!=feedback_id)):
            if normalize(existing.question)==normalize(body.question.strip()) and source_scope & {source['document_id'] for source in existing.sources}:
                raise HTTPException(409,'该文档范围内已有同一标准问题，请先编辑或撤回已有正式知识，避免答案冲突。')
        row=session.scalar(select(PublishedKnowledge).where(PublishedKnowledge.feedback_id==feedback_id).with_for_update())
        if (row.revision if row else 0)!=body.expected_revision:
            raise HTTPException(409,'发布版本已变化，请刷新后重新审核。')
        if not row:
            row=PublishedKnowledge(feedback_id=feedback_id,revision=1)
            session.add(row)
        else:
            changed=session.execute(update(PublishedKnowledge).where(PublishedKnowledge.id==row.id,PublishedKnowledge.revision==body.expected_revision).values(revision=body.expected_revision+1))
            if not changed.rowcount:
                raise HTTPException(409,'发布版本已变化，请刷新后重新审核。')
        row.question,row.answer,row.sources=body.question.strip(),body.answer.strip(),sources
        row.status,row.review_note,row.updated_at='published',body.review_note.strip(),now()
        feedback.status,feedback.resolution,feedback.updated_at='resolved',body.review_note.strip(),now()
        try:
            session.flush()
            payload=serialize_knowledge(session,row)
            session.add(PublicationEvent(knowledge_id=row.id,action='publish',snapshot=payload))
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409,'此反馈已发布，请刷新后重试。')
        return payload


class WithdrawInput(BaseModel):
    reason: str = Field(min_length=1,max_length=2000)
    expected_revision: int = Field(ge=1)


@router.post('/feedback/{feedback_id}/withdraw')
@serialized_publication
def withdraw(feedback_id: str, body: WithdrawInput):
    if not body.reason.strip():
        raise HTTPException(422,'请填写撤回原因。')
    with Session() as session:
        row=session.scalar(select(PublishedKnowledge).where(PublishedKnowledge.feedback_id==feedback_id).with_for_update())
        if not row:
            raise HTTPException(404,'尚未发布正式知识。')
        if row.revision!=body.expected_revision:
            raise HTTPException(409,'版本已变化，请刷新。')
        if row.status != 'published':
            raise HTTPException(409,'该正式知识已撤回，无需重复操作。')
        changed=session.execute(update(PublishedKnowledge).where(PublishedKnowledge.id==row.id,PublishedKnowledge.revision==body.expected_revision,PublishedKnowledge.status=='published').values(revision=body.expected_revision+1))
        if not changed.rowcount:
            raise HTTPException(409,'发布版本已变化，请刷新。')
        row.status,row.review_note,row.updated_at='withdrawn',body.reason.strip(),now()
        feedback=session.get(AnswerFeedback,feedback_id)
        feedback.status,feedback.resolution,feedback.updated_at='in_progress',body.reason.strip(),now()
        payload=serialize_knowledge(session,row)
        session.add(PublicationEvent(knowledge_id=row.id,action='withdraw',snapshot=payload))
        session.commit()
        return payload


@router.get('/feedback/{feedback_id}/publication-history')
def publication_history(feedback_id: str):
    with Session() as session:
        row=session.scalar(select(PublishedKnowledge).where(PublishedKnowledge.feedback_id==feedback_id))
        if not row:
            return []
        return [{'action':e.action,'snapshot':e.snapshot} for e in session.scalars(select(PublicationEvent).where(PublicationEvent.knowledge_id==row.id).order_by(PublicationEvent.created_at.desc(),PublicationEvent.id))]


def retrieve_approved(question, document_ids, limit=2):
    from .text import normalize, tokens, GENERIC
    from .retrieval import lexical_rank
    with Session() as session:
        rows=list(session.scalars(select(PublishedKnowledge).where(PublishedKnowledge.status=='published')))
        rows=[row for row in rows if (not document_ids or {s['document_id'] for s in row.sources}.issubset(set(document_ids))) and active_sources(session,row.sources)]
        query_terms=set(tokens(question))-GENERIC
        candidates=[]
        for row in rows:
            terms=set(tokens(row.question))-GENERIC
            overlap=len(query_terms & terms)/max(1,len(query_terms))
            if normalize(question)==normalize(row.question) or (overlap>=.7 and len(query_terms & terms)>=2):
                candidates.append(row)
        ranked=lexical_rank(question,[SimpleNamespace(heading=row.question,content=row.answer) for row in candidates])
        hits=[]
        used_chunks=set()
        for index,score in ranked[:limit]:
            row=candidates[index]
            clean=re.sub(r'\[\d+\]','',row.answer)
            if used_chunks & {source['chunk_id'] for source in row.sources}:
                continue
            if len(hits)+len(row.sources)>limit:
                continue
            used_chunks.update(source['chunk_id'] for source in row.sources)
            for source_index,source in enumerate(row.sources):
                content=('人工审核标准答案：\n'+clean if source_index==0 else '人工审核标准答案的补充来源')+'\n原文依据：\n'+source['content'][:450]
                hits.append({'id':len(hits)+1,'chunk_id':source['chunk_id'],'document_id':source['document_id'],
                             'document_name':source['document_name'],'page':source['page'],'revision':source['revision'],
                             'heading':'已审核问答 · '+row.question,'content':content,
                             'matched_content':clean if source_index==0 else source['content'][:450],'kind':'approved_qa','score':score,
                             'knowledge_id':row.id,'knowledge_revision':row.revision,'source_references':row.sources})
        return hits


@router.get('/knowledge/approved')
def approved_knowledge():
    with Session() as session:
        rows=session.scalars(select(PublishedKnowledge).order_by(PublishedKnowledge.updated_at.desc()).limit(100))
        return [serialize_knowledge(session,row) | {'feedback_id':row.feedback_id} for row in rows]


def exact_approved_answer(question, document_ids):
    from .text import normalize
    def key(value):
        return re.sub(r'\s+', '', re.sub(r'[?！!。？]+$', '', normalize(value).strip()))
    with Session() as session:
        matches=[]
        for row in session.scalars(select(PublishedKnowledge).where(PublishedKnowledge.status=='published')):
            if key(question)!=key(row.question):
                continue
            if document_ids and not {source['document_id'] for source in row.sources}.issubset(set(document_ids)):
                continue
            if active_sources(session,row.sources):
                matches.append(row)
        # 多个产品存在同名问题时，不直接选择其中一个产品的答案。
        if len(matches)!=1:
            return None
        row=matches[0]
        metadata={'id':row.id,'revision':row.revision,'question':row.question,'feedback_id':row.feedback_id}
        citations=[dict(source,kind='approved_qa',score=1.0,knowledge_id=row.id,knowledge_revision=row.revision) for source in row.sources]
        return {'answer':row.answer,'citations':citations,'metadata':metadata}
