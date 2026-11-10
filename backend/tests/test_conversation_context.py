from fastapi.testclient import TestClient

from app.conversation_context import prompt_context, rewrite_query, select_history
from app.db import Message
from app.main import app
from app.retrieval import lexical_rank
from app.db import Chunk


def test_follow_up_uses_recent_topic_and_numbered_answer():
    history = [
        {'role': 'user', 'content': '手机钥匙如何使用？'},
        {'role': 'assistant', 'content': '1. 打开蓝牙\n2. 绑定车辆'},
    ]
    assert rewrite_query('第二种怎么设置？', history) == '手机钥匙如何使用？ 2. 绑定车辆 第二种怎么设置？'
    assert rewrite_query('RS485 通信失败怎么处理？', history) == 'RS485 通信失败怎么处理？'


def test_history_keeps_current_scope_and_prompt_budget():
    messages = [
        Message(role='user', content='旧产品怎么设置？'),
        Message(role='assistant', content='旧产品答案', status='complete', request_context={'document_ids': ['old']}),
        Message(role='user', content='手机钥匙如何使用？'),
        Message(role='assistant', content='操作步骤' * 700, status='complete', request_context={'document_ids': ['new']}),
    ]
    history = select_history(messages, ['new'])
    assert history[0]['content'] == '手机钥匙如何使用？'
    assert len(history[1]['content']) == 1100
    assert sum(len(item['content']) for item in history) <= 4800


def test_context_budget_preserves_source_identifiers():
    citations = [
        {'id': 1, 'document_name': '手册.pdf', 'page': 3, 'heading': '手机钥匙', 'content': '绑定方法' * 400},
        {'id': 2, 'document_name': '手册.pdf', 'page': 4, 'heading': '限制', 'content': '限制条件' * 400},
    ]
    context = prompt_context(citations, max_chars=2000)
    assert '[1]' in context and '[2]' in context
    assert len(context) <= 2000


def test_duplicate_request_does_not_duplicate_messages():
    with TestClient(app) as client:
        body = {'question': '不存在的知识问题', 'request_id': 'repeat-request-1'}
        response = client.post('/api/chat', json=body)
        assert response.status_code == 200
        conversation_id = response.text.split('"conversation_id": "')[1].split('"')[0]
        assert client.post('/api/chat', json=body).status_code == 409
        messages = client.get(f'/api/conversations/{conversation_id}/messages').json()
        assert len(messages) == 2
        assert messages[0]['status'] == 'complete'


def test_follow_up_retrieval_keeps_product_topic(monkeypatch):
    from app import main

    queries = []

    async def retrieve_stub(question, *_args, **_kwargs):
        queries.append(question)
        return [], 'keyword', ''

    monkeypatch.setattr(main, 'retrieve', retrieve_stub)
    with TestClient(app) as client:
        first = client.post('/api/chat', json={'question': '手机钥匙如何使用？'})
        conversation_id = first.text.split('"conversation_id": "')[1].split('"')[0]
        client.post('/api/chat', json={'question': '它有哪些限制？', 'conversation_id': conversation_id})
    assert queries == ['手机钥匙如何使用？', '手机钥匙如何使用？ 它有哪些限制？']


def test_follow_up_query_improves_local_top_hit():
    chunks = [
        Chunk(content='手机钥匙的使用限制包括手机蓝牙状态和支持的机型。'),
        Chunk(content='机械钥匙的使用限制包括钥匙齿形和锁芯磨损。'),
    ]
    history = [{'role': 'user', 'content': '手机钥匙如何使用？'}, {'role': 'assistant', 'content': '请查看绑定步骤。'}]
    query = rewrite_query('它有哪些限制？', history)
    assert lexical_rank(query, chunks)[0][0] == 0
