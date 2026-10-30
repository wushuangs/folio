from fastapi.testclient import TestClient
from app.main import app
from app.db import Document, Chunk, Session, uid


def add_doc(name, heading):
    with Session() as s:
        d=Document(name=name,sha256=uid(),size=100,status='ready',active_revision=1)
        s.add(d);s.flush()
        s.add(Chunk(document_id=d.id,revision=1,page=3,ordinal=1,heading=heading,content='这是当前文档的真实说明内容。'))
        s.commit()
        return d.id


def test_suggestions_follow_scope_and_revision():
    with TestClient(app) as client:
        first=add_doc('设备A.pdf','1.2 电池维护')
        second=add_doc('设备B.pdf','3 充电操作')
        def get(ids):return client.post('/api/suggestions',json={'document_ids':ids}).json()['items']
        a=get([first]);b=get([second])
        assert a and all(x['document_id']==first for x in a)
        assert '电池维护' in a[0]['text'] and '充电操作' in b[0]['text']
        both=get([first,second]);assert {x['document_id'] for x in both}=={first,second}
        with Session() as s:
            d=s.get(Document,first);d.active_revision=2
            s.add(Chunk(document_id=first,revision=2,page=4,ordinal=1,heading='2 网络设置',content='新的网络设置说明。'));s.commit()
        assert '网络设置' in get([first])[0]['text']
        with Session() as s:
            d=s.get(Document,first);d.status='archived';s.commit()
        assert get([first])==[]
        assert get(['nonexistent'])==[]


def test_suggestions_use_body_when_headings_are_missing():
    with TestClient(app) as client:
        doc=add_doc('没有章节.pdf','')
        data=client.post('/api/suggestions',json={'document_ids':[doc]}).json()
        assert data['mode']=='local' and '真实说明内容' in data['items'][0]['text']
        assert client.post('/api/suggestions',json={'document_ids':['a']*101}).status_code==422
