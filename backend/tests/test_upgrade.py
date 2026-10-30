import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import ingestion
from app.db import Chunk, Document, Session, uid, init_db, sync_fts
from app.main import app, finalize_answer
from app.retrieval import retrieve


@pytest.fixture
def version_document():
    init_db()
    docid=uid()
    with Session() as session:
        doc=Document(id=docid,name='版本测试.pdf',sha256=docid,size=1,status='ready',pages=1,chunks=1,embedding_signature='old-model')
        session.add(doc);session.flush()
        session.add(Chunk(document_id=doc.id,page=1,ordinal=0,heading='通信地址',content='通信地址默认1，范围1-254。',embedding=[1.,0.]))
        session.commit()
    return docid


def test_versions_failure_rollback_archive(monkeypatch,version_document):
    docid=version_document
    with TestClient(app) as client:
        def broken(*_):
            raise ValueError('解析失败测试')
        monkeypatch.setattr(ingestion,'parse_pdf',broken)
        assert not asyncio.run(ingestion.ingest(docid,'text'))
        with Session() as session:
            doc=session.get(Document,docid)
            assert doc.status=='ready' and doc.active_revision==0 and doc.embedding_signature=='old-model'
            assert '旧索引' in doc.error
            assert session.scalar(select(Chunk).where(Chunk.document_id==doc.id)).embedding==[1.,0.]
        monkeypatch.setattr(ingestion,'parse_pdf',lambda *_:(1,[dict(page=1,ordinal=0,heading='通信地址',content='通信地址默认2，范围1-255。',context='',kind='table_row',group_key='p1t1')]))
        from app.providers import ProviderError
        async def rate_limited(*_):
            raise ProviderError('向量服务调用受限（HTTP 429）')
        monkeypatch.setattr(ingestion,'embed',rate_limited)
        monkeypatch.setattr(ingestion,'embedding_signature',lambda:'test-new-model')
        assert not asyncio.run(ingestion.ingest(docid,'full'))
        with Session() as session:
            doc=session.get(Document,docid)
            assert doc.active_revision==0 and doc.status=='ready' and '429' in doc.error

        assert asyncio.run(ingestion.ingest(docid,'text'))
        assert {v['revision'] for v in client.get(f'/api/documents/{docid}/revisions').json()}=={0,1}
        assert '默认2' in asyncio.run(retrieve('通信地址默认值',[docid],allow_remote=False))[0][0]['content']
        assert client.post(f'/api/documents/{docid}/rollback',json={'revision':0}).status_code==200
        assert '默认1' in asyncio.run(retrieve('通信地址默认值',[docid],allow_remote=False))[0][0]['content']
        assert client.delete(f'/api/documents/{docid}').status_code==200
        assert not asyncio.run(retrieve('通信地址默认值',[docid],allow_remote=False))[0]
        assert client.post(f'/api/documents/{docid}/restore').status_code==200
        assert asyncio.run(retrieve('通信地址默认值',[docid],allow_remote=False))[0]


def test_citation_validation():
    citations=[{'id':1,'document_name':'文档.pdf','page':5}]
    assert finalize_answer('没有来源的结论',citations)[2]=='no_evidence'
    assert finalize_answer('错误引用[9]',citations)[2]=='no_evidence'
    result=finalize_answer('有依据[1]',citations)
    assert result[2]=='generated' and 'PDF 第 5 页' in result[0]


def test_local_mode_never_calls_provider(monkeypatch,version_document):
    docid=version_document
    from app import main, retrieval
    async def forbidden(*_):
        raise AssertionError('本地模式不得发送内容到模型')
        yield
    async def forbidden_embed(*_):
        raise AssertionError('本地模式不得发送问题到模型')
    monkeypatch.setattr(main,'stream_answer',forbidden)
    monkeypatch.setattr(retrieval,'embed',forbidden_embed)
    monkeypatch.setenv('LLM_API_KEY','fake');monkeypatch.setenv('LLM_MODEL','fake')
    with TestClient(app) as client:
        response=client.post('/api/chat',json={'question':'通信地址默认值','document_ids':[docid],'answer_mode':'extractive'})
        assert 'event: done' in response.text and 'event: error' not in response.text
        assert '"mode": "extractive"' in response.text


def test_guard_and_scope():
    with TestClient(app) as client:
        assert client.post('/api/chat',json={'question':'hi'},headers={'origin':'https://evil.example'}).status_code==403
        assert client.post('/api/documents',content=b'x',headers={'content-length':str(60*1024*1024)}).status_code==413
        assert client.post('/api/chat',json={'question':'hi','document_ids':['missing']}).status_code==409
        assert client.post('/api/chat',json={'question':' '*5}).status_code==400


def test_table_geometry_preserves_columns_and_blank_cells():
    import pdfplumber
    from pathlib import Path
    from app.parsing import table_records
    manual=next((Path(__file__).resolve().parents[2]/'downloads').glob('*.pdf'))
    with pdfplumber.open(manual) as pdf:
        voltage=table_records(pdf.pages[7].find_tables()[0])
        assert '项目：工作电压' in voltage[1] and 'UL认证' in voltage[1]
        assert '分组：' not in voltage[2] and '整机功耗' in voltage[2]
        terminals=table_records(pdf.pages[98].find_tables()[0])
        assert '开关量输出口2' not in terminals[2] and '电源负极' in terminals[2]
        parameters=table_records(pdf.pages[49].find_tables()[0])
        row=next(r for r in parameters if '项目：通信地址' in r)
        assert '参数范围：(1-254)' in row and '默认值：1' in row and '描述：远程监控' in row


def test_queue_restart_resumes_persisted_job(monkeypatch,version_document):
    docid=version_document
    import time
    from app.db import IndexJob, init_db
    init_db()
    monkeypatch.setattr(ingestion,'parse_pdf',lambda *_:(1,[dict(page=1,ordinal=0,heading='通信地址',content='通信地址默认3，范围1-255。',context='',kind='table_row',group_key='p1t1')]))
    with Session() as session:
        doc=session.get(Document,docid);doc.indexing_status='processing'
        job=IndexJob(document_id=doc.id,mode='text',status='running')
        session.add(job);session.commit();jobid=job.id
    with TestClient(app):
        for _ in range(100):
            with Session() as session:
                if session.get(IndexJob,jobid).status=='succeeded': break
            time.sleep(.02)
        with Session() as session:
            assert session.get(IndexJob,jobid).status=='succeeded'
            assert session.get(Document,docid).indexing_status=='idle'


def test_common_aliases_and_unknown_protocols(version_document):
    docid=version_document
    from app.text import normalize
    assert normalize('RS-485连不上')=='rs485通信失败'
    assert '贮存温度' in normalize('设备放仓库的最低温度')
    with TestClient(app):
        assert not asyncio.run(retrieve('控制器如何连接WiFi',[docid],allow_remote=False))[0]


def test_retrieval_vector_failure_falls_back_with_warning(monkeypatch,version_document):
    from app import retrieval
    from app.providers import ProviderError
    monkeypatch.setattr(retrieval,'embedding_signature',lambda:'old-model')
    async def failed(*_):
        raise ProviderError('向量服务调用受限（HTTP 429）')
    monkeypatch.setattr(retrieval,'embed',failed)
    with TestClient(app):
        hits,mode,warning=asyncio.run(retrieve('通信地址默认值',[version_document]))
        assert hits and mode=='keyword' and '429' in warning and '降级' in warning


def test_database_vector_retrieval(monkeypatch,version_document):
    from app import retrieval
    monkeypatch.setattr(retrieval,'embedding_signature',lambda:'old-model')
    async def vector(*_):
        return [[1.,0.]]
    monkeypatch.setattr(retrieval,'embed',vector)
    with TestClient(app):
        hits,mode,warning=asyncio.run(retrieve('通信地址默认值',[version_document]))
        assert hits and mode=='hybrid' and not warning
        assert all('embedding' not in hit for hit in hits)
