from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.db import Conversation, Message, Session, now
from app import quality


def seed():
    with Session() as session:
        c = Conversation(title='feedback', document_ids=[])
        session.add(c); session.flush()
        session.add(Message(conversation_id=c.id,role='user',content='测试问题', created_at=now()-timedelta(seconds=1)))
        m = Message(conversation_id=c.id,role='assistant',content='原回答 [1]', citations=[{'document_id':'test-doc','page':9,'revision':1}], retrieval_mode='keyword')
        session.add(m);session.commit()
        return m.id


def test_feedback_snapshot_upsert_and_resolution():
    with TestClient(app) as client:
        mid = seed()
        assert client.get(f'/api/messages/{mid}/feedback').json() is None
        assert client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem'}).status_code == 422
        response = client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'citation','comment':'引用不对'})
        assert response.status_code == 200
        row = response.json()
        assert row['snapshot']['question'] == '测试问题'
        assert row['snapshot']['citations'][0]['revision'] == 1
        assert client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'citation','comment':'引用不对'}).json()['id'] == row['id']
        assert client.patch(f'/api/feedback/{row["id"]}',json={'status':'resolved'}).status_code == 422
        assert client.patch(f'/api/feedback/{row["id"]}',json={'status':'resolved','resolution':'已核对原文'}).json()['status'] == 'resolved'
        with Session() as session:
            m=session.get(Message,mid);m.content='changed';session.commit()
        assert client.get(f'/api/messages/{mid}/feedback').json()['snapshot']['answer']=='原回答 [1]'
        changed=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()
        assert changed['status']=='open' and not changed['resolution']
        assert client.get('/api/feedback?rating=problem&status=open&limit=1').json()['total']>=1
        assert client.get('/api/feedback?limit=101').status_code==422
        assert client.put('/api/messages/nonexistent/feedback',json={'rating':'helpful'}).status_code==404


def test_verification_keeps_status_and_original_answer(monkeypatch):
    async def retrieve(*args, **kwargs):
        assert kwargs['allow_remote'] is False
        return [], 'keyword', ''
    monkeypatch.setattr(quality,'retrieve',retrieve)
    with TestClient(app) as client:
        mid=seed()
        row=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'missing'}).json()
        result=client.post(f'/api/feedback/{row["id"]}/verify',json={'mode':'local'})
        assert result.status_code==200 and result.json()['answer_mode']=='retrieval_only'
        current=client.get(f'/api/messages/{mid}/feedback').json()
        assert current['status']=='open' and current['verification']['checked_at']
        assert current['snapshot']['answer']=='原回答 [1]'
        assert client.post(f'/api/feedback/{row["id"]}/verify',json={'mode':'live'}).status_code==409


def test_verification_provider_failure_preserves_feedback(monkeypatch):
    from app.providers import ProviderError
    from app import config
    async def fail(*args, **kwargs):
        raise ProviderError('向量服务调用受限（HTTP 429）')
    monkeypatch.setattr(quality,'retrieve',fail)
    monkeypatch.setattr(config,'settings',lambda:{'llm_key':'mock-only'})
    with TestClient(app) as client:
        mid=seed()
        row=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()
        result=client.post(f'/api/feedback/{row["id"]}/verify',json={'mode':'live'})
        assert result.status_code==502 and '429' in result.json()['detail']
        current=client.get(f'/api/messages/{mid}/feedback').json()
        assert current['snapshot']==row['snapshot'] and current['status']=='open'
        assert row['id'] not in quality._verifying


def test_new_answer_request_context_keeps_original_scope():
    with TestClient(app) as client:
        mid=seed()
        with Session() as session:
            m=session.get(Message,mid)
            m.request_context={'question':'那默认值呢？','document_ids':['original-scope'],'history':[{'role':'user','content':'电池欠压阈值'}]}
            c=session.get(Conversation,m.conversation_id);c.document_ids=['different-scope'];session.commit()
        row=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()
        assert row['snapshot']['question']=='那默认值呢？'
        assert row['snapshot']['document_ids']==['original-scope']
        assert row['snapshot']['history'][0]['content']=='电池欠压阈值'
        assert row['updated_at'].endswith('+00:00')
