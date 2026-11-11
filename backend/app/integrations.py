import hashlib
import hmac
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import resolve_model
from .db import Conversation, Document, KnowledgeApp, Message, Session, now
from .publication import exact_approved_answer
from .retrieval import retrieve

router=APIRouter(prefix='/api')


def new_key():
    secret='folio_'+secrets.token_urlsafe(32)
    return secret,hashlib.sha256(secret.encode()).hexdigest()


def public_app(row):
    return {'id':row.id,'name':row.name,'document_ids':row.document_ids,
            'model_id':row.model_id,'answer_mode':row.answer_mode,
            'retrieval':row.retrieval,'key_prefix':row.key_prefix,
            'enabled':row.enabled,'created_at':row.created_at.isoformat(),
            'updated_at':row.updated_at.isoformat()}


def validate_scope(session,document_ids):
    if len(set(document_ids))!=len(document_ids):
        raise HTTPException(422,'文档范围不能重复。')
    found=set(session.scalars(select(Document.id).where(
        Document.id.in_(document_ids),Document.status=='ready')))
    if found!=set(document_ids):
        raise HTTPException(409,'部分文档不可用，请刷新文档列表。')


def validate_model(model_id):
    try:
        resolve_model(model_id)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


class AppInput(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    document_ids:list[str]=Field(min_length=1,max_length=100)
    model_id:str=Field(default='default',max_length=64)
    answer_mode:str=Field(default='auto',pattern='^(auto|extractive)$')
    retrieval:str=Field(default='auto',pattern='^(auto|keyword)$')


@router.get('/apps')
def list_apps():
    with Session() as session:
        return [public_app(row) for row in session.scalars(
            select(KnowledgeApp).order_by(KnowledgeApp.created_at.desc()))]


@router.post('/apps')
def create_app(body:AppInput):
    if not body.name.strip():
        raise HTTPException(422,'请输入应用名称。')
    validate_model(body.model_id)
    secret,digest=new_key()
    with Session() as session:
        validate_scope(session,body.document_ids)
        row=KnowledgeApp(name=body.name.strip(),document_ids=body.document_ids,
                         model_id=body.model_id,answer_mode=body.answer_mode,
                         retrieval=body.retrieval,key_hash=digest,key_prefix=secret[:14])
        session.add(row)
        session.commit()
        return public_app(row) | {'api_key':secret}


class AppUpdate(BaseModel):
    name:str|None=Field(default=None,max_length=100)
    document_ids:list[str]|None=Field(default=None,min_length=1,max_length=100)
    model_id:str|None=Field(default=None,max_length=64)
    answer_mode:str|None=Field(default=None,pattern='^(auto|extractive)$')
    retrieval:str|None=Field(default=None,pattern='^(auto|keyword)$')
    enabled:bool|None=None


@router.patch('/apps/{app_id}')
def update_app(app_id:str,body:AppUpdate):
    with Session() as session:
        row=session.get(KnowledgeApp,app_id)
        if not row:
            raise HTTPException(404,'应用不存在。')
        if body.document_ids is not None:
            validate_scope(session,body.document_ids)
            row.document_ids=body.document_ids
        if body.model_id is not None:
            validate_model(body.model_id)
            row.model_id=body.model_id
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(422,'应用名称不能为空。')
            row.name=body.name.strip()
        for field in ['answer_mode','retrieval','enabled']:
            value=getattr(body,field)
            if value is not None:
                setattr(row,field,value)
        row.updated_at=now()
        session.commit()
        return public_app(row)


@router.post('/apps/{app_id}/rotate-key')
def rotate_app_key(app_id:str):
    with Session() as session:
        row=session.get(KnowledgeApp,app_id)
        if not row:
            raise HTTPException(404,'应用不存在。')
        secret,digest=new_key()
        row.key_hash,row.key_prefix,row.updated_at=digest,secret[:14],now()
        session.commit()
        return public_app(row) | {'api_key':secret}


def authorized_app(app_id,authorization):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'请提供应用 API Key。')
    token=authorization[7:].strip()
    if not token:
        raise HTTPException(401,'请提供应用 API Key。')
    digest=hashlib.sha256(token.encode()).hexdigest()
    with Session() as session:
        row=session.get(KnowledgeApp,app_id)
        if not row or not hmac.compare_digest(digest,row.key_hash):
            raise HTTPException(401,'应用凭证无效。')
        if not row.enabled:
            raise HTTPException(403,'应用已停用。')
        return public_app(row)


class IntegrationChatInput(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    conversation_id:str|None=None
    request_id:str|None=Field(default=None,max_length=64)


@router.post('/integrations/{app_id}/chat')
async def integration_chat(app_id:str,body:IntegrationChatInput,
                           authorization:str|None=Header(default=None)):
    application=authorized_app(app_id,authorization)
    from .main import Question, run_chat
    request_id=hashlib.sha256((app_id+':'+body.request_id).encode()).hexdigest() if body.request_id else None
    return await run_chat(Question(
        question=body.question,conversation_id=body.conversation_id,
        request_id=request_id,document_ids=application['document_ids'],
        model_id=application['model_id'],answer_mode=application['answer_mode'],
        retrieval=application['retrieval']),app_id)


class IntegrationSearchInput(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    retrieval:str=Field(default='auto',pattern='^(auto|keyword)$')


@router.post('/integrations/{app_id}/search')
async def integration_search(app_id:str,body:IntegrationSearchInput,
                             authorization:str|None=Header(default=None)):
    application=authorized_app(app_id,authorization)
    with Session() as session:
        validate_scope(session,application['document_ids'])
    from .conversation_context import rewrite_query
    query=rewrite_query(body.question,[])
    if application['answer_mode']!='extractive':
        exact=exact_approved_answer(body.question,application['document_ids'])
        if exact:
            return {'query':query,'answer_path':'published_exact',
                    'retrieval_mode':'approved','warning':'',
                    'citations':exact['citations']}
    hits,mode,warning=await retrieve(
        query,application['document_ids'],
        allow_remote=body.retrieval!='keyword' and application['retrieval']!='keyword' and application['answer_mode']!='extractive',
        include_approved=application['answer_mode']!='extractive')
    return {'query':query,'answer_path':'retrieval','retrieval_mode':mode,
            'warning':warning,'citations':hits}


@router.get('/integrations/{app_id}/conversations/{conversation_id}/messages')
def integration_messages(app_id:str,conversation_id:str,
                         authorization:str|None=Header(default=None)):
    authorized_app(app_id,authorization)
    from .main import serialize_message
    with Session() as session:
        conversation=session.get(Conversation,conversation_id)
        if not conversation or conversation.app_id!=app_id:
            raise HTTPException(404,'会话不存在。')
        rows=session.scalars(select(Message).where(
            Message.conversation_id==conversation_id).order_by(Message.created_at,Message.id))
        return [serialize_message(row) for row in rows]
