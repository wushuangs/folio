import hashlib
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Document, Session, SyncBinding
from app.main import app


def _wait_for_sync(client,path,expected_id):
    for _ in range(80):
        with Session() as session:
            binding=session.get(SyncBinding,path)
            document=session.get(Document,expected_id)
            if binding and binding.document_id==expected_id and not binding.pending_document_id and document.status=='ready':
                return
        time.sleep(.1)
    raise AssertionError('同步索引未在时限内完成')


def test_local_folder_sync_keeps_old_index_until_new_one_is_ready(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='folio-sync-source-') as directory:
        root=Path(directory)
        file=root/'manual.txt'
        first=b'The calibration interval is seventeen months.'
        second=b'The calibration interval is nineteen months.'
        file.write_bytes(first)
        monkeypatch.setenv('LOCAL_SYNC_DIR',str(root))
        with TestClient(app) as client:
            preview=client.get('/api/sync/preview').json()
            assert preview['items'][0]['status']=='new'
            assert client.post('/api/sync/apply',json={
                'relative_path':'../outside.txt','expected_sha256':'a'*64}).status_code==400
            applied=client.post('/api/sync/apply',json={
                'relative_path':'manual.txt','expected_sha256':hashlib.sha256(first).hexdigest()})
            assert applied.status_code==200
            first_id=applied.json()['document_id']
            _wait_for_sync(client,'manual.txt',first_id)
            assert client.get('/api/sync/preview').json()['items'][0]['status']=='unchanged'
            application=client.post('/api/apps',json={
                'name':'同步知识应用','document_ids':[first_id],
                'answer_mode':'extractive','retrieval':'keyword'}).json()
            app_id=application['id']

            file.write_bytes(second)
            changed=client.get('/api/sync/preview').json()['items'][0]
            assert changed['status']=='changed'
            applied=client.post('/api/sync/apply',json={
                'relative_path':'manual.txt','expected_sha256':changed['sha256']})
            assert applied.status_code==200
            second_id=applied.json()['document_id']
            with Session() as session:
                assert session.get(Document,first_id).status=='ready'
            _wait_for_sync(client,'manual.txt',second_id)
            with Session() as session:
                assert session.get(Document,first_id).status=='archived'
                assert session.get(Document,second_id).status=='ready'
            assert client.get('/api/apps').json()[0]['document_ids']==[second_id]
            rollback=client.post('/api/sync/rollback',json={'relative_path':'manual.txt'})
            assert rollback.status_code==200
            with Session() as session:
                assert session.get(Document,first_id).status=='ready'
                assert session.get(Document,second_id).status=='archived'
            assert next(row for row in client.get('/api/apps').json() if row['id']==app_id)['document_ids']==[first_id]
            assert client.get('/api/sync/preview').json()['items'][0]['status']=='changed'
            file.write_bytes(b'short')
            assert client.post('/api/sync/apply',json={
                'relative_path':'manual.txt','expected_sha256':hashlib.sha256(second).hexdigest()
            }).status_code==409
            failed=client.post('/api/sync/apply',json={
                'relative_path':'manual.txt','expected_sha256':hashlib.sha256(b'short').hexdigest()})
            assert failed.status_code==200
            failed_id=failed.json()['document_id']
            for _ in range(80):
                with Session() as session:
                    failed_doc=session.get(Document,failed_id)
                    binding=session.get(SyncBinding,'manual.txt')
                    if failed_doc.status=='error' and not binding.pending_document_id:
                        break
                time.sleep(.1)
            with Session() as session:
                assert session.get(Document,first_id).status=='ready'
                assert session.get(SyncBinding,'manual.txt').document_id==first_id
