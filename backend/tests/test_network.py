import asyncio
import socket
import ssl

import httpx
import pytest
from fastapi.testclient import TestClient

from app import providers
from app.main import app


def test_connect_error_classification_hides_private_details():
    private='fake-private-key-and-proxy-password'
    error=httpx.ConnectError(f'[Errno 8] nodename nor servname provided {private}')
    assert 'DNS' in str(providers.connection_error('向量服务',error))
    assert private not in str(providers.connection_error('向量服务',error))
    assert '代理' in str(providers.connection_error('向量服务',httpx.ProxyError(private)))
    assert '证书' in str(providers.connection_error('向量服务',httpx.ConnectError('certificate verify failed '+private)))
    assert '权限' in str(providers.connection_error('向量服务',httpx.ConnectError('Operation not permitted '+private)))


def test_transient_connection_error_retries_without_disabling_tls(monkeypatch):
    original=httpx.AsyncClient
    calls=[]
    async def sleep(_): pass
    def handle(request):
        calls.append(request)
        if len(calls)<3:
            raise httpx.ConnectError('[Errno 8] nodename nor servname provided',request=request)
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.,0.]}]})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    assert asyncio.run(providers.embed(['local-mock']))==[[1.,0.]]
    assert len(calls)==3


def test_persistent_dns_failure_is_bounded(monkeypatch):
    original=httpx.AsyncClient
    calls=[]
    async def sleep(_): pass
    def handle(request):
        calls.append(request)
        raise httpx.ConnectError('[Errno 8] nodename nor servname provided',request=request)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    with pytest.raises(providers.ProviderError,match='DNS'):
        asyncio.run(providers.embed(['local-mock']))
    assert len(calls)==3


def test_network_probe_sends_no_credentials_or_document(monkeypatch):
    original=httpx.AsyncClient
    seen=[]
    def handle(request):
        seen.append(request)
        assert request.method=='GET' and not request.content
        assert 'authorization' not in request.headers
        return httpx.Response(401)
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setenv('EMBEDDING_API_KEY','fake-private-key')
    with TestClient(app) as client:
        result=client.get('/api/health/network').json()
        assert result['reachable'] is True and result['http_status']==401
        assert 'fake-private-key' not in str(result)
    assert len(seen)==1
