import asyncio
from datetime import timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import AnswerFeedback, Conversation, Message, Session, now
from .providers import ProviderError, stream_answer
from .retrieval import retrieve

router = APIRouter(prefix='/api')


class FeedbackInput(BaseModel):
    rating: Literal['helpful', 'problem']
    category: Literal['', 'incorrect', 'citation', 'missing', 'other'] = ''
    comment: str = Field(default='', max_length=2000)


class FeedbackUpdate(BaseModel):
    status: Literal['open', 'in_progress', 'resolved']
    resolution: str = Field(default='', max_length=4000)


class VerificationInput(BaseModel):
    mode: Literal['local', 'live'] = 'local'
    model_id: str | None = Field(default=None,max_length=64)
    question: str | None = Field(default=None, min_length=1, max_length=2000)


def serialize(row):
    data = {key: getattr(row, key) for key in [
        'id', 'message_id', 'rating', 'category', 'comment', 'status',
        'resolution', 'snapshot', 'verification', 'created_at', 'updated_at']}
    for key in ['created_at', 'updated_at']:
        value = data[key]
        data[key] = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    from .db import PublishedKnowledge
    from .publication import serialize_knowledge, publication_sources
    with Session() as session:
        published = session.scalar(select(PublishedKnowledge).where(PublishedKnowledge.feedback_id == row.id))
        data['publication'] = serialize_knowledge(session, published)
        data['publication_sources'] = publication_sources(session, row, published)
    return data


@router.get('/messages/{message_id}/feedback')
def get_feedback(message_id: str):
    with Session() as session:
        row = session.scalar(select(AnswerFeedback).where(AnswerFeedback.message_id == message_id))
        return serialize(row) if row else None


@router.get('/feedback/{feedback_id}')
def get_feedback_by_id(feedback_id: str):
    with Session() as session:
        row = session.get(AnswerFeedback, feedback_id)
        if not row:
            raise HTTPException(404, '反馈不存在。')
        return serialize(row)


@router.put('/messages/{message_id}/feedback')
def submit_feedback(message_id: str, body: FeedbackInput):
    if body.rating == 'problem' and not body.category:
        raise HTTPException(422, '请选择问题类型。')
    with Session() as session:
        message = session.get(Message, message_id)
        if not message or message.role != 'assistant':
            raise HTTPException(404, '回答不存在。')
        question = session.scalar(select(Message).where(
            Message.conversation_id == message.conversation_id,
            Message.role == 'user', Message.created_at <= message.created_at,
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
        conversation = session.get(Conversation, message.conversation_id)
        row = session.scalar(select(AnswerFeedback).where(AnswerFeedback.message_id == message_id))
        if not row:
            row = AnswerFeedback(message_id=message_id, snapshot={
                'question': message.request_context.get('question') or (question.content if question else ''),
                'history': message.request_context.get('history', []),
                'answer': message.content, 'citations': message.citations,
                'conversation_id': message.conversation_id,
                'document_ids': message.request_context.get('document_ids', list(dict.fromkeys(c['document_id'] for c in message.citations)) or conversation.document_ids),
                'model_id': message.request_context.get('model_id','default'),
                'model': message.request_context.get('model'),
                'mode': message.mode, 'retrieval_mode': message.retrieval_mode,
                'warning': message.warning,
            })
            session.add(row)
        changed = row.rating != body.rating or row.category != body.category or row.comment != body.comment.strip()
        row.rating, row.category, row.comment = body.rating, body.category if body.rating == 'problem' else '', body.comment.strip()
        if changed:
            row.status = 'open' if body.rating == 'problem' else 'resolved'
            row.resolution = ''
            row.verification = {}
        row.updated_at = now()
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, '反馈已更新，请刷新后重试。')
        return serialize(row)


@router.get('/feedback')
def list_feedback(rating: Literal['all', 'helpful', 'problem'] = 'all',
                  status: Literal['all', 'open', 'in_progress', 'resolved'] = 'all',
                  offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
    with Session() as session:
        query = select(AnswerFeedback)
        if rating != 'all':
            query = query.where(AnswerFeedback.rating == rating)
        if status != 'all':
            query = query.where(AnswerFeedback.status == status)
        from sqlalchemy import func
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(query.order_by(AnswerFeedback.updated_at.desc(), AnswerFeedback.id).offset(offset).limit(limit))
        return {'total': total, 'items': [serialize(row) for row in items]}


@router.patch('/feedback/{feedback_id}')
def update_feedback(feedback_id: str, body: FeedbackUpdate):
    if body.status == 'resolved' and not body.resolution.strip():
        raise HTTPException(422, '标记解决前请填写处理说明。')
    with Session() as session:
        row = session.get(AnswerFeedback, feedback_id)
        if not row:
            raise HTTPException(404, '反馈不存在。')
        row.status, row.resolution, row.updated_at = body.status, body.resolution.strip(), now()
        session.commit()
        return serialize(row)


_verifying: set[str] = set()


@router.post('/feedback/{feedback_id}/verify')
async def verify_feedback(feedback_id: str, body: VerificationInput):
    if feedback_id in _verifying:
        raise HTTPException(409, '该问题正在验证中。')
    with Session() as session:
        row = session.get(AnswerFeedback, feedback_id)
        if not row:
            raise HTTPException(404, '反馈不存在。')
        snapshot = dict(row.snapshot)
        updated_at = row.updated_at
    _verifying.add(feedback_id)
    try:
        from .main import finalize_answer, rewrite_query, source_location
        from .config import resolve_model
        try:
            profile=resolve_model(body.model_id)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
        if body.mode == 'live' and not (profile['api_key'] or profile.get('local')):
            raise HTTPException(409, '未配置回答模型，请先使用本地验证。')
        question = (body.question if body.question is not None else snapshot['question']).strip()
        if not question:
            raise HTTPException(422, '验证问题不能为空。')
        history = snapshot.get('history', []) if question == snapshot['question'] else []
        async with asyncio.timeout(120):
            query = rewrite_query(question, history)
            hits, mode, warning = await retrieve(query, snapshot['document_ids'], allow_remote=body.mode == 'live', include_approved=False)
            answer, answer_mode = '', 'retrieval_only'
            if body.mode == 'live' and hits:
                context = '\n\n'.join(f'[{c["id"]}] {c["document_name"]}，{source_location(c)}\n{c["content"]}' for c in hits)
                async for delta in (stream_answer(question, context, history, model_id=body.model_id) if body.model_id else stream_answer(question, context, history)):
                    answer += delta
                if not answer.strip():
                    raise ProviderError('模型返回空回答。')
                answer, _, answer_mode, extra = finalize_answer(answer, hits)
                warning = ' '.join(filter(None, [warning, extra]))
            elif body.mode == 'live':
                answer, answer_mode = '当前资料不足，未调用生成模型。', 'no_evidence'
        result = {'checked_at': now().isoformat(), 'question': question, 'query': query, 'included_approved': False, 'requested_mode': body.mode,
                  'retrieval_mode': mode, 'answer_mode': answer_mode, 'citations': hits,
                  'answer': answer, 'warning': warning,
                  'model_id': profile['id'] if body.mode=='live' else None,
                  'note': '检索结果仅供核对。确认正确后审核发布，无需发布时填写原因关闭反馈。'}
        with Session() as session:
            row = session.get(AnswerFeedback, feedback_id)
            if row.updated_at != updated_at:
                raise HTTPException(409, '验证期间反馈已更新，请重新验证。')
            row.verification, row.updated_at = result, now()
            session.commit()
        return result
    except ProviderError as exc:
        raise HTTPException(502, str(exc))
    except TimeoutError:
        raise HTTPException(504, '验证超时，请稍后重试。')
    finally:
        _verifying.discard(feedback_id)


@router.get('/feedback-insights')
def feedback_insights():
    from .text import tokens, GENERIC
    from collections import Counter
    with Session() as session:
        rows=list(session.scalars(select(AnswerFeedback).where(AnswerFeedback.rating=='problem').order_by(AnswerFeedback.updated_at.desc()).limit(2000)))
        groups=[]
        for row in rows:
            question=row.snapshot.get('question','')
            terms=set(tokens(question))-GENERIC
            scope=set(row.snapshot.get('document_ids',[]))
            group=next((g for g in groups if g['_scope']==scope and (g['question']==question or len(g['_terms'] & terms)/max(1,len(g['_terms'] | terms))>=.55)),None)
            if not group:
                group={'question':question,'ids':[],'open_count':0,'categories':Counter(),'_terms':terms,'_scope':scope}
                groups.append(group)
            group['ids'].append(row.id)
            group['open_count']+=row.status!='resolved'
            group['categories'][row.category]+=1
        return {'sample_size':len(rows),'limit':2000,'groups':[{'question':g['question'],'ids':g['ids'],'count':len(g['ids']),'open_count':g['open_count'],'categories':dict(g['categories'])} for g in sorted(groups,key=lambda g:len(g['ids']),reverse=True) if len(g['ids'])>=2], 'note':'按文档范围与词语相似度在本地归组，仅用于发现线索，不自动合并或判定根因。'}


class BatchCloseInput(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=4000)


from .publication import serialized_publication


@router.post('/feedback-batch-close')
@serialized_publication
def batch_close(body: BatchCloseInput):
    from .db import PublishedKnowledge
    if not body.reason.strip():raise HTTPException(422,'请填写关闭原因。')
    with Session() as session:
        ids=list(dict.fromkeys(body.ids))
        rows=list(session.scalars(select(AnswerFeedback).where(AnswerFeedback.id.in_(ids))))
        if len(rows)!=len(ids):raise HTTPException(409,'部分反馈已不存在，本批次未执行。')
        if session.scalar(select(PublishedKnowledge.id).where(PublishedKnowledge.feedback_id.in_(ids),PublishedKnowledge.status=='published')):
            raise HTTPException(409,'包含已发布知识，请在知识库审核维护，不能批量关闭。')
        for row in rows:row.status,row.resolution,row.updated_at='resolved',body.reason.strip(),now()
        session.commit()
        return {'updated':len(rows)}
