from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Chunk, Document, Message, Session, sync_fts
from app.main import app
from app.retrieval import invalidate_cache
from app.text import content_terms


def test_diagnosis_explains_retrieval_without_creating_chat(monkeypatch):
    with TestClient(app) as client:
        with Session() as session:
            document=Document(name='测试手册.pdf',sha256='diagnosis-'+'a'*54,size=100,
                              pages=2,chunks=2,status='ready',active_revision=1)
            session.add(document)
            session.flush()
            first=Chunk(document_id=document.id,page=1,ordinal=0,revision=1,
                        heading='手机钥匙',content='手机钥匙需要打开蓝牙并完成绑定。',
                        search_terms=content_terms('手机钥匙需要打开蓝牙并完成绑定。'))
            second=Chunk(document_id=document.id,page=2,ordinal=1,revision=1,
                         heading='机械钥匙',content='机械钥匙插入锁芯后旋转。',
                         search_terms=content_terms('机械钥匙插入锁芯后旋转。'))
            session.add_all([first,second])
            session.flush()
            sync_fts(session,document.id)
            session.commit()
            document_id=document.id
            expected_chunk=first.id
            messages_before=session.scalar(select(func.count()).select_from(Message))
        invalidate_cache()
        result=client.post('/api/retrieval/diagnose',json={
            'question':'手机钥匙如何使用？','document_ids':[document_id],
            'retrieval':'keyword'})
        assert result.status_code==200
        payload=result.json()
        assert payload['answer_path']=='retrieval'
        assert payload['retrieval_mode']=='keyword'
        assert payload['trace']['corpus_chunks']==2
        assert payload['trace']['lexical'][0]['chunk_id']==expected_chunk
        assert payload['trace']['fusion'][0]['selected'] is True
        assert payload['trace']['final'][0]['chunk_id']==expected_chunk
        assert payload['trace']['vector_state']=='disabled'
        assert payload['trace']['timings_ms']['total']>=0
        with Session() as session:
            assert session.scalar(select(func.count()).select_from(Message))==messages_before
        invalid=client.post('/api/retrieval/diagnose',json={
            'question':'测试','document_ids':['missing']})
        assert invalid.status_code==409
        blank=client.post('/api/retrieval/diagnose',json={'question':'  '})
        assert blank.status_code==422

        from app import retrieval
        from app.providers import ProviderError
        monkeypatch.setattr(retrieval,'embedding_signature',lambda:'test-vectors')
        with Session() as session:
            session.get(Document,document_id).embedding_signature='test-vectors'
            session.get(Chunk,expected_chunk).embedding=[1.0,0.0]
            session.get(Chunk,second.id).embedding=[0.0,1.0]
            session.commit()
        invalidate_cache()

        async def embed_ok(_texts):
            return [[1.0,0.0]]
        monkeypatch.setattr(retrieval,'embed',embed_ok)
        hybrid=client.post('/api/retrieval/diagnose',json={
            'question':'手机钥匙如何使用？','document_ids':[document_id]})
        assert hybrid.status_code==200
        trace=hybrid.json()['trace']
        assert hybrid.json()['retrieval_mode']=='hybrid'
        assert trace['vector_state']=='used'
        assert trace['vector'][0]['chunk_id']==expected_chunk
        assert trace['vector'][0]['included'] is True

        async def embed_failed(_texts):
            raise ProviderError('模拟向量服务不可用')
        monkeypatch.setattr(retrieval,'embed',embed_failed)
        fallback=client.post('/api/retrieval/diagnose',json={
            'question':'手机钥匙如何使用？','document_ids':[document_id]})
        assert fallback.status_code==200
        assert fallback.json()['trace']['vector_state']=='failed'
        assert fallback.json()['retrieval_mode']=='keyword'
        assert '模拟向量服务不可用' in fallback.json()['warning']


def test_diagnosis_shows_exact_published_answer_without_retrieval(monkeypatch):
    from app import publication
    monkeypatch.setattr(publication,'exact_approved_answer',lambda *_: {
        'answer':'审核答案',
        'metadata':{'question':'标准问题','revision':2},
        'citations':[{'id':1,'chunk_id':'source-1','document_id':'doc-1',
                      'document_name':'手册.pdf','page':3,'heading':'使用说明',
                      'content':'原文依据'}]})
    with TestClient(app) as client:
        result=client.post('/api/retrieval/diagnose',json={
            'question':'标准问题','retrieval':'keyword'})
    assert result.status_code==200
    assert result.json()['answer_path']=='published_exact'
    assert result.json()['trace']['vector_state']=='skipped'
    assert result.json()['trace']['final'][0]['content']=='原文依据'
