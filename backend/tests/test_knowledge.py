import io
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.db import Chunk, Document, Session
from app.ingestion import split_page
from app.main import app
from app.retrieval import lexical_rank, tokens


def parse_events(response):
    events = []
    for packet in response.text.strip().split('\n\n'):
        lines = packet.splitlines()
        kind = lines[0].removeprefix('event: ').strip()
        data = json.loads(lines[1].removeprefix('data: '))
        events.append((kind,data))
    return dict(events)


def test_chunking_never_loses_end():
    text = '参数与说明\n' * 400 + 'END_MARKER'
    chunks = split_page(text)
    assert len(chunks)>1
    assert chunks[-1].endswith('END_MARKER')
    assert all(len(c)<=800 for c in chunks)


def test_exact_codes_and_chinese_retrieval():
    chunks = [Chunk(content='RS485 不能正常通信时，检查 COM 端口设置和 A、B 接线。'),Chunk(content='启动失败时检查燃油供应。')]
    assert 'rs485' in tokens('RS485 通信失败')
    assert lexical_rank('RS485 不能正常通信', chunks)[0][0] == 0
    assert lexical_rank('北京今天的天气预报', chunks) == []


def test_upload_retrieve_history_and_citations():
    manual = next((Path(__file__).resolve().parents[2]/'downloads').glob('*.pdf'))
    with TestClient(app) as client:
        assert client.get('/api/health').json()['mode'] == 'extractive'
        with manual.open('rb') as file:
            response = client.post('/api/documents',files={'file':(manual.name,file,'application/pdf')})
        assert response.status_code == 202
        document_id = response.json()['id']
        for _ in range(1800):
            documents = client.get('/api/documents').json()
            if documents[0]['status'] in ['ready','error']: break
            time.sleep(.1)
        assert documents[0]['status']=='ready'
        assert documents[0]['pages']==100
        assert documents[0]['chunks']>100
        with manual.open('rb') as file:
            duplicated = client.post('/api/documents',files={'file':(manual.name,file,'application/pdf')})
        assert duplicated.json()['id']==document_id
        assert len(client.get('/api/documents').json())==1
        result = parse_events(client.post('/api/chat',json={'question':'RS485 不能正常通信时应该检查什么？','document_ids':[document_id]}))
        assert 'error' not in result
        citations = result['sources']['citations']
        assert citations and any(c['page']==100 for c in citations)
        assert result['done']['mode']=='extractive'
        assert 'RS485' in result['delta']['text']
        assert all(1<=c['page']<=100 for c in citations)
        conversation_id = result['start']['conversation_id']
        history = client.get(f'/api/conversations/{conversation_id}/messages').json()
        assert len(history)==2
        assert history[1]['citations']==citations
        assert client.get(f'/api/documents/{document_id}/file',headers={'Range':'bytes=0-15'}).status_code==206
        assert client.post('/api/documents',files={'file':('fake.pdf',b'not a pdf','application/pdf')}).status_code==400
        no_evidence = parse_events(client.post('/api/chat',json={'question':'北京今天的天气预报'}))
        assert no_evidence['done']['mode']=='no_evidence'
        assert client.post('/api/chat',json={'question':'hi','conversation_id':'missing'}).status_code==404
        # 已加密文件必须报告错误，不能以已就绪状态留下空索引。
        writer=PdfWriter(); writer.add_blank_page(width=100,height=100); writer.encrypt('secret')
        buffer=io.BytesIO();writer.write(buffer)
        encrypted=client.post('/api/documents',files={'file':('locked.pdf',buffer.getvalue(),'application/pdf')}).json()
        for _ in range(100):
            listed=client.get('/api/documents').json()
            if next(d for d in listed if d['id']==encrypted['id'])['status']=='error': break
            time.sleep(.1)
        assert next(d for d in listed if d['id']==encrypted['id'])['status']=='error'
        with Session() as session:
            assert session.scalar(select(Document).where(Document.id==document_id)).status=='ready'
            assert session.scalar(select(Chunk).where(Chunk.document_id==document_id))


def test_model_stream_and_embeddings_protocol(monkeypatch):
    import asyncio
    import httpx
    from app import providers

    original_client = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200,json={'data':[{'index':1,'embedding':[0.,1.]},{'index':0,'embedding':[1.,0.]}]})
        return httpx.Response(200,content='data: {"choices":[{"delta":{"reasoning_content":"internal"}}]}\n\ndata: {"choices":[{"delta":{"content":"检查接线 [1]"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',headers={'content-type':'text/event-stream'})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw: original_client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv('LLM_API_KEY','test-key')
    monkeypatch.setenv('LLM_MODEL','glm-4.7')
    monkeypatch.setenv('EMBEDDING_API_KEY','test-key')
    monkeypatch.setenv('EMBEDDING_MODEL','embedding-3')
    async def check():
        assert await providers.embed(['a','b']) == [[1.,0.],[0.,1.]]
        assert ''.join([delta async for delta in providers.stream_answer('通信失败','[1] 检查接线',[])]) == '检查接线 [1]'
    asyncio.run(check())
    assert seen[1]['thinking']=={'type':'disabled'}
    assert 'api_key' not in seen[1]


def test_interrupted_model_stream_is_not_success(monkeypatch):
    import asyncio
    import httpx
    import pytest
    from app import providers
    original_client=httpx.AsyncClient
    transport=httpx.MockTransport(lambda _:httpx.Response(200,content='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'))
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original_client(transport=transport,**kw))
    async def check():
        with pytest.raises(providers.ProviderError,match='中断'):
            async for _ in providers.stream_answer('q','context',[]):
                pass
    asyncio.run(check())


def test_generated_answer_and_provider_error_persistence(monkeypatch):
    from app import main
    from app.providers import ProviderError
    monkeypatch.setenv('LLM_API_KEY','test-key')
    monkeypatch.setenv('LLM_MODEL','glm-4.7')
    async def answer(question,context,history):
        assert '[1]' in context and 'COM' in context
        yield '检查 COM 端口设置 [1]。'
    monkeypatch.setattr(main,'stream_answer',answer)
    with TestClient(app) as client:
        with Session() as session:
            doc=Document(id='generation-fixture',name='通信说明.pdf',sha256='generation-fixture',size=1,status='ready',pages=1,chunks=1)
            session.add(doc); session.flush()
            session.add(Chunk(document_id=doc.id,page=1,ordinal=0,heading='通信',content='RS485 故障时检查 COM 端口设置。'))
            session.commit()
        result=parse_events(client.post('/api/chat',json={'question':'RS485 故障','document_ids':['generation-fixture']}))
        assert result['done']['mode']=='generated'
        history=client.get(f'/api/conversations/{result["start"]["conversation_id"]}/messages').json()
        assert history[-1]['content'].startswith('检查 COM 端口设置 [1]。')
        assert history[-1]['citations'][0]['page']==1
        async def failed(*_):
            raise ProviderError('测试服务不可用')
            yield
        monkeypatch.setattr(main,'stream_answer',failed)
        result=parse_events(client.post('/api/chat',json={'question':'RS485 故障','document_ids':['generation-fixture']}))
        assert 'error' in result and 'done' not in result
        history=client.get(f'/api/conversations/{result["start"]["conversation_id"]}/messages').json()
        assert len(history)==2 and history[-1]['status']=='error' and history[-1]['citations']==[]


def test_authentication_failure_is_actionable_and_redacted(monkeypatch):
    import asyncio
    import httpx
    import pytest
    from app import providers
    original_client = httpx.AsyncClient
    secret = 'fake-sensitive-key'
    transport = httpx.MockTransport(lambda _: httpx.Response(401, json={'error': {'message': secret}}))
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kw: original_client(transport=transport, **kw))
    monkeypatch.setenv('EMBEDDING_API_KEY', secret)
    async def check():
        with pytest.raises(providers.ProviderError) as error:
            await providers.embed(['测试'])
        assert 'HTTP 401' in str(error.value)
        assert '鉴权失败' in str(error.value)
        assert secret not in str(error.value)
        with pytest.raises(providers.ProviderError, match='HTTP 401'):
            async for _ in providers.stream_answer('问题', '资料', []):
                pass
    asyncio.run(check())


def test_embedding_quota_failure_does_not_retry(monkeypatch):
    import asyncio
    import httpx
    import pytest
    from app import providers
    original_client = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(429,json={'error':{'code':'1113','message':'private error text'}})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
    async def check():
        with pytest.raises(providers.ProviderError, match='余额不足') as caught:
            await providers.embed(['test'])
        assert '1113' in str(caught.value)
        assert 'private error text' not in str(caught.value)
    asyncio.run(check())
    assert len(requests)==1


def test_embedding_transient_rate_limit_retries_then_succeeds(monkeypatch):
    import asyncio
    import httpx
    from app import providers
    original_client=httpx.AsyncClient
    requests=[]
    delays=[]
    def handler(request):
        requests.append(request)
        if len(requests)<3:
            return httpx.Response(429,json={'error':{'code':'1302'}},headers={'Retry-After':'2'})
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.,0.]}]})
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    assert asyncio.run(providers.embed(['test']))==[[1.,0.]]
    assert len(requests)==3 and delays==[2.,2.]


def test_embedding_rate_limit_retry_is_bounded(monkeypatch):
    import asyncio
    import httpx
    import pytest
    from app import providers
    original_client=httpx.AsyncClient
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(429,json={'error':{'code':'1305'}})
    async def sleep(_):
        pass
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    async def check():
        with pytest.raises(providers.ProviderError, match='1305'):
            await providers.embed(['test'])
    asyncio.run(check())
    assert len(requests)==4
