import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Chunk, Conversation, Document, Message, PublishedKnowledge, Session, uid
from app.retrieval import retrieve, invalidate_cache


def seed():
    with Session() as session:
        doc=Document(name='测试设备.pdf',sha256=uid(),size=100,status='ready',active_revision=1)
        session.add(doc);session.flush()
        c=Chunk(document_id=doc.id,revision=1,page=8,ordinal=0,heading='设备维护周期',content='设备维护周期为十七个月。',context='设备维护周期为十七个月。',search_terms='设备 维护 周期 十七 个月')
        session.add(c)
        conv=Conversation(title='设备维护周期',document_ids=[doc.id]);session.add(conv);session.flush()
        m=Message(conversation_id=conv.id,role='assistant',content='旧回答',request_context={'question':'设备维护周期是多少？','document_ids':[doc.id]},citations=[])
        session.add(m);session.flush()
        m.citations=[{'id':1,'chunk_id':c.id,'document_id':doc.id,'document_name':doc.name,'revision':1,'page':8,'heading':c.heading,'content':c.content}]
        session.commit();invalidate_cache()
        return doc.id,m.id,c.id


def body(chunk_id,revision=0):
    return {'question':'设备维护周期是多少？','answer':'设备维护周期为十七个月，需按维护规范执行。[1]','source_set':'snapshot','source_ids':[chunk_id],'review_note':'人工已核对原文与适用条件','confirmed':True,'expected_revision':revision}


def test_publish_retrieve_withdraw_and_audit():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        row=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()
        fid=row['id'];payload=body(chunk)
        assert client.post(f'/api/feedback/{fid}/publish',json={**payload,'confirmed':False}).status_code==422
        assert client.post(f'/api/feedback/{fid}/publish',json={**payload,'source_ids':['invented']}).status_code==422
        published=client.post(f'/api/feedback/{fid}/publish',json=payload)
        assert published.status_code==200,published.text
        assert published.json()['retrieval_active'] and published.json()['revision']==1
        assert client.get(f'/api/messages/{mid}/feedback').json()['status']=='resolved'
        assert any(k['id']==published.json()['id'] for k in client.get('/api/knowledge/approved').json())
        hits,_,_=asyncio.run(retrieve(payload['question'],[doc],allow_remote=False))
        assert hits[0]['kind']=='approved_qa' and '维护规范执行' in hits[0]['matched_content']
        assert hits[0]['document_id']==doc and hits[0]['page']==8
        response=client.post('/api/chat',json={'question':payload['question'],'document_ids':[doc],'answer_mode':'auto'})
        assert response.status_code==200 and '维护规范执行' in response.text and 'approved_qa' in response.text
        assert asyncio.run(retrieve(payload['question'],['another-doc'],allow_remote=False))[0]==[]
        assert client.post(f'/api/feedback/{fid}/publish',json=payload).status_code==409
        result=client.post(f'/api/feedback/{fid}/withdraw',json={'reason':'验收撤回','expected_revision':1})
        assert result.status_code==200 and not result.json()['retrieval_active']
        assert all(h['kind']!='approved_qa' for h in asyncio.run(retrieve(payload['question'],[doc],allow_remote=False))[0])
        history=client.get(f'/api/feedback/{fid}/publication-history').json()
        assert [e['action'] for e in history]==['withdraw','publish']
        assert history[1]['snapshot']['answer']==payload['answer']


def test_stale_sources_pause_and_prevent_publication():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        row=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()
        fid=row['id'];payload=body(chunk)
        assert client.post(f'/api/feedback/{fid}/publish',json={**payload,'answer':'答案 [99]'}).status_code==422
        assert client.post(f'/api/feedback/{fid}/publish',json=payload).status_code==200
        with Session() as session:
            d=session.get(Document,doc);d.status='archived';session.commit()
        assert not client.get(f'/api/messages/{mid}/feedback').json()['publication']['retrieval_active']
        assert asyncio.run(retrieve(payload['question'],[doc],allow_remote=False))[0]==[]
        assert client.post(f'/api/feedback/{fid}/publish',json=body(chunk,1)).status_code==409
        with Session() as session:
            d=session.get(Document,doc);d.status='ready';d.active_revision=2;session.commit()
        assert not client.get(f'/api/messages/{mid}/feedback').json()['publication']['retrieval_active']
        assert client.post(f'/api/feedback/{fid}/publish',json=body(chunk,1)).status_code==409


def test_update_versions_and_duplicate_question_conflict():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        first=client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).json()
        updated=body(chunk,1);updated['answer']='维护周期是十七个月，标准答案已复核。[1]'
        second=client.post(f'/api/feedback/{fid}/publish',json=updated)
        assert second.status_code==200 and second.json()['id']==first['id'] and second.json()['revision']==2
        with Session() as session:
            original=session.get(Message,mid)
            other=Message(conversation_id=original.conversation_id,role='assistant',content='另一次回答',citations=original.citations,request_context=original.request_context)
            session.add(other);session.commit();other_id=other.id
        duplicate=client.put(f'/api/messages/{other_id}/feedback',json={'rating':'helpful'}).json()['id']
        assert client.post(f'/api/feedback/{duplicate}/publish',json=body(chunk)).status_code==409
        history=client.get(f'/api/feedback/{fid}/publication-history').json()
        assert len(history)==2 and history[0]['snapshot']['answer']==updated['answer']
        assert history[1]['snapshot']['answer']==body(chunk)['answer']


def test_independent_verification_and_legacy_self_citations():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        assert client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).status_code==200
        from app.db import AnswerFeedback
        with Session() as session:
            f=session.get(AnswerFeedback,fid)
            f.verification={'answer_mode':'retrieval_only','citations':[dict(f.snapshot['citations'][0],kind='approved_qa'),dict(f.snapshot['citations'][0],id=4)]}
            session.commit()
        detail=client.get(f'/api/feedback/{fid}').json()
        assert detail['publication_sources']['published']['items'][0]['id']==1
        assert not detail['publication_sources']['verification']['items']
        assert '已发布问答' in detail['publication_sources']['verification']['warning']
        request=body(chunk,1);request['source_set']='verification'
        assert client.post(f'/api/feedback/{fid}/publish',json=request).status_code==409
        request['source_set']='published'
        assert client.post(f'/api/feedback/{fid}/publish',json=request).status_code==200
        result=client.post(f'/api/feedback/{fid}/verify',json={'mode':'local','question':'设备维护周期是多少？'}).json()
        assert result['included_approved'] is False
        assert result['citations'] and all(c['kind']!='approved_qa' for c in result['citations'])
        assert result['answer']=='' and result['question']=='设备维护周期是多少？'
        assert client.post(f'/api/feedback/{fid}/verify',json={'mode':'local','question':' '}).status_code==422


def test_publish_reports_exact_missing_reference_and_stale_source():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        payload=body(chunk);payload['answer']='答案 [6]'
        response=client.post(f'/api/feedback/{fid}/publish',json=payload)
        assert response.status_code==422 and '[6]' in response.json()['detail']
        with Session() as session:
            d=session.get(Document,doc);d.active_revision=2;session.commit()
        source=client.get(f'/api/feedback/{fid}').json()['publication_sources']['snapshot']['items'][0]
        assert source['is_current'] is False and '版本' in source['source_issue']


def test_concurrent_publication_updates_use_one_revision():
    from concurrent.futures import ThreadPoolExecutor
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        assert client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).status_code==200
        def submit(_):
            return client.post(f'/api/feedback/{fid}/publish',json=body(chunk,1)).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(submit,[1,2]))
        assert sorted(statuses)==[200,409]
        assert len(client.get(f'/api/feedback/{fid}/publication-history').json())==2


def test_same_question_returns_exact_published_answer_without_model(monkeypatch):
    import json
    from app import main
    async def forbidden(*args, **kwargs):
        raise AssertionError('Exact approved answer must not call retrieval or model')
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        payload=body(chunk);payload['answer']='### 结论\n审核专用标记：维护周期十七个月。[1]\n\n请按确认流程执行。'
        publication=client.post(f'/api/feedback/{fid}/publish',json=payload).json()
        monkeypatch.setattr(main,'retrieve',forbidden)
        monkeypatch.setattr(main,'stream_answer',forbidden)
        response=client.post('/api/chat',json={'question':payload['question'],'document_ids':[doc]})
        events={}
        for packet in response.text.split('\n\n'):
            lines=packet.splitlines()
            event=next((line[7:] for line in lines if line.startswith('event: ')),None)
            data=next((json.loads(line[6:]) for line in lines if line.startswith('data: ')),None)
            if event:events[event]=data
        assert events['delta']['text']==payload['answer']
        assert events['done']['mode']=='approved' and events['done']['approved_knowledge']['revision']==1
        cid=events['start']['conversation_id']
        saved=client.get(f'/api/conversations/{cid}/messages').json()[-1]
        assert saved['content']==payload['answer'] and saved['approved_knowledge']['id']==publication['id']
        assert [c['id'] for c in saved['citations']]==[1]


def test_exact_answer_respects_scope_status_and_raw_only_mode():
    from app.publication import exact_approved_answer
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        payload=body(chunk)
        assert client.post(f'/api/feedback/{fid}/publish',json=payload).status_code==200
        assert exact_approved_answer('设备维护周期是多少',[doc])['answer']==payload['answer']
        assert exact_approved_answer(payload['question'],['other-doc']) is None
        assert exact_approved_answer(payload['question']+'请补充退款流程',[doc]) is None
        raw=client.post('/api/chat',json={'question':payload['question'],'document_ids':[doc],'answer_mode':'extractive'})
        assert 'approved_qa' not in raw.text and '维护规范执行' not in raw.text
        client.post(f'/api/feedback/{fid}/withdraw',json={'reason':'测试撤回','expected_revision':1})
        assert exact_approved_answer(payload['question'],[doc]) is None
