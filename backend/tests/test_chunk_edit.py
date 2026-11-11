from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Chunk, Document, Revision, Session, sync_fts
from app.main import app
from app.publication import active_sources
from app.retrieval import invalidate_cache
from app.text import content_terms


def test_chunk_correction_is_versioned_searchable_and_reversible():
    with TestClient(app) as client:
        with Session() as session:
            doc=Document(name='修正测试.txt',sha256='b'*64,size=50,file_type='txt',
                         pages=1,chunks=2,status='ready',active_revision=1)
            session.add(doc)
            session.flush()
            first=Chunk(document_id=doc.id,revision=1,page=1,ordinal=0,heading='操作',
                        content='蓝牙钥匙需要开启网络。',context='蓝牙钥匙需要开启网络。',
                        search_terms=content_terms('操作 蓝牙钥匙需要开启网络。'))
            second=Chunk(document_id=doc.id,revision=1,page=1,ordinal=1,heading='限制',
                         content='设备电量不足时无法使用。',context='设备电量不足时无法使用。',
                         search_terms=content_terms('限制 设备电量不足时无法使用。'))
            session.add_all([first,second])
            session.add(Revision(document_id=doc.id,revision=1,pages=1,chunks=2))
            session.flush()
            sync_fts(session,doc.id)
            session.commit()
            doc_id,first_id=doc.id,first.id
        invalidate_cache()
        result=client.patch(f'/api/documents/{doc_id}/chunks/{first_id}',json={
            'expected_revision':1,'content':'蓝牙钥匙需要开启蓝牙。','enabled':True,
            'reason':'原文识别错误','rebuild_vectors':False})
        assert result.status_code==200
        assert result.json()['revision']==2
        replacement=result.json()['chunk_id']
        with Session() as session:
            doc=session.get(Document,doc_id)
            assert doc.active_revision==2
            assert session.get(Chunk,first_id).content=='蓝牙钥匙需要开启网络。'
            assert session.get(Chunk,replacement).content=='蓝牙钥匙需要开启蓝牙。'
            assert not active_sources(session,[{
                'document_id':doc_id,'chunk_id':first_id,'revision':1}])
            assert session.scalar(select(Revision).where(
                Revision.document_id==doc_id,Revision.revision==2))
        preview=client.get(f'/api/documents/{doc_id}/chunks?page=1').json()
        assert any(row['id']==replacement and row['enabled'] for row in preview)
        assert client.get(f'/api/documents/{doc_id}/chunk-edits').json()[0]['reason']=='原文识别错误'
        stale=client.patch(f'/api/documents/{doc_id}/chunks/{first_id}',json={
            'expected_revision':1,'content':'过期修改','enabled':True,'reason':'旧版本'})
        assert stale.status_code in {404,409}
        assert client.post(f'/api/documents/{doc_id}/rollback',json={'revision':1}).status_code==200
        with Session() as session:
            assert active_sources(session,[{
                'document_id':doc_id,'chunk_id':first_id,'revision':1}])


def test_disabled_chunk_is_excluded_from_diagnosis():
    with TestClient(app) as client:
        with Session() as session:
            doc=Document(name='停用测试.txt',sha256='c'*64,size=50,file_type='txt',
                         pages=1,chunks=1,status='ready',active_revision=1)
            session.add(doc)
            session.flush()
            chunk=Chunk(document_id=doc.id,revision=1,page=1,ordinal=0,heading='私有功能',
                        content='特殊秘钥功能可用。',context='特殊秘钥功能可用。',
                        search_terms=content_terms('私有功能 特殊秘钥功能可用。'))
            session.add(chunk)
            session.add(Revision(document_id=doc.id,revision=1,pages=1,chunks=1))
            session.flush()
            sync_fts(session,doc.id)
            session.commit()
            doc_id,chunk_id=doc.id,chunk.id
        invalidate_cache()
        disabled=client.patch(f'/api/documents/{doc_id}/chunks/{chunk_id}',json={
            'expected_revision':1,'content':'特殊秘钥功能可用。','enabled':False,
            'reason':'内容已过期','rebuild_vectors':False})
        assert disabled.status_code==200
        result=client.post('/api/retrieval/diagnose',json={
            'question':'特殊秘钥功能','document_ids':[doc_id],'retrieval':'keyword'})
        assert result.status_code==200
        assert result.json()['trace']['corpus_chunks']==0
        assert result.json()['trace']['final']==[]
