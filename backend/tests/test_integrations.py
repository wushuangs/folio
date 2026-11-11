import json

from fastapi.testclient import TestClient

from app.db import Chunk, Document, Revision, Session, sync_fts
from app.main import app
from app.retrieval import invalidate_cache
from app.text import content_terms


def _events(response):
    result={}
    for packet in response.text.strip().split('\n\n'):
        lines=packet.splitlines()
        if len(lines)>1:
            result[lines[0].removeprefix('event: ').strip()]=json.loads(
                lines[1].removeprefix('data: '))
    return result


def test_app_key_limits_chat_search_and_conversation_scope():
    with TestClient(app) as client:
        with Session() as session:
            doc=Document(name='业务手册.txt',sha256='d'*64,size=50,file_type='txt',
                         pages=1,chunks=1,status='ready',active_revision=1)
            session.add(doc)
            session.flush()
            content='专属校准设备的保修期限为十七个月。'
            session.add(Chunk(document_id=doc.id,revision=1,page=1,ordinal=0,
                              heading='保修期限',content=content,context=content,
                              search_terms=content_terms('保修期限 '+content)))
            session.add(Revision(document_id=doc.id,revision=1,pages=1,chunks=1))
            session.flush()
            sync_fts(session,doc.id)
            session.commit()
            document_id=doc.id
        invalidate_cache()
        created=client.post('/api/apps',json={
            'name':'售后知识入口','document_ids':[document_id],
            'answer_mode':'extractive','retrieval':'keyword'})
        assert created.status_code==200
        application=created.json()
        app_id,key=application['id'],application['api_key']
        assert key.startswith('folio_')
        assert 'api_key' not in client.get('/api/apps').json()[0]
        search_url=f'/api/integrations/{app_id}/search'
        body={'question':'专属校准设备保修期限是多少？'}
        assert client.post(search_url,json=body).status_code==401
        assert client.post(search_url,json=body,headers={
            'Authorization':'Bearer wrong'}).status_code==401
        searched=client.post(search_url,json=body,headers={
            'Authorization':'Bearer '+key})
        assert searched.status_code==200
        assert searched.json()['citations'][0]['document_id']==document_id
        chat=client.post(f'/api/integrations/{app_id}/chat',json=body,headers={
            'Authorization':'Bearer '+key})
        assert chat.status_code==200
        events=_events(chat)
        assert events['done']['mode']=='extractive'
        conversation_id=events['start']['conversation_id']
        assert client.get(f'/api/conversations/{conversation_id}/messages').status_code==404
        assert client.get(f'/api/integrations/{app_id}/conversations/{conversation_id}/messages',
                          headers={'Authorization':'Bearer '+key}).status_code==200
        other=client.post('/api/apps',json={
            'name':'另一个应用','document_ids':[document_id]}).json()
        assert client.get(f'/api/integrations/{other["id"]}/conversations/{conversation_id}/messages',
                          headers={'Authorization':'Bearer '+other['api_key']}).status_code==404
        assert client.post(f'/api/integrations/{other["id"]}/chat',json={
            'question':'继续','conversation_id':conversation_id},
            headers={'Authorization':'Bearer '+other['api_key']}).status_code==404
        rotated=client.post(f'/api/apps/{app_id}/rotate-key').json()
        assert rotated['api_key']!=key
        assert client.post(search_url,json=body,headers={
            'Authorization':'Bearer '+key}).status_code==401
        assert client.patch(f'/api/apps/{app_id}',json={'enabled':False}).status_code==200
        assert client.post(search_url,json=body,headers={
            'Authorization':'Bearer '+rotated['api_key']}).status_code==403
