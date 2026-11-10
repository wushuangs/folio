import asyncio
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app, finalize_answer
from app.documents import parse_document, quality_report, validate_file
from app.config import public_models, resolve_model
from app import providers, ocr
from app.db import AnswerFeedback, Conversation, Document, Message, PublishedKnowledge, Session, uid
from app.publication import exact_approved_answer
from test_publication import seed, body


def archive(path, files):
    with zipfile.ZipFile(path,'w') as z:
        for name,value in files.items():z.writestr(name,value)


@pytest.mark.parametrize('extension,content',[('txt','测试设备工作电压为24V，维护前断开电源。'),('md','# 设备手册\n测试设备工作电压为24V，维护前断开电源。'),('csv','名称,默认值,单位\n工作电压,24,V\n预热时间,10,s')])
def test_text_formats(tmp_path,extension,content):
    path=tmp_path/f'file.{extension}';path.write_text(content)
    validate_file(path,'.'+extension)
    pages,chunks,report=parse_document(path,extension)
    assert pages>=1 and chunks and report['characters']>10
    assert '24' in ' '.join(c['content'] for c in chunks)


def test_docx_preserves_tables(tmp_path):
    path=tmp_path/'manual.docx'
    archive(path,{'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>设备使用说明：维护前请断开电源。</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>电压</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>24 V</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
    _,chunks,_=parse_document(path,'docx')
    assert '电压 | 24 V' in chunks[0]['content']


def test_xlsx_preserves_empty_columns(tmp_path):
    path=tmp_path/'manual.xlsx'
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    archive(path,{'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="参数表" sheetId="1" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{ns}"><sheetData><row><c r="A1" t="inlineStr"><is><t>名称</t></is></c><c r="C1" t="inlineStr"><is><t>电压</t></is></c></row><row><c r="A2" t="inlineStr"><is><t>设备额定参数</t></is></c><c r="C2"><v>24</v></c></row></sheetData></worksheet>'})
    _,chunks,_=parse_document(path,'xlsx')
    assert '电压：24' in chunks[0]['content'] and '参数表' in chunks[0]['heading']


def test_bad_upload_and_text_roundtrip():
    with TestClient(app) as client:
        assert client.post('/api/documents',files={'file':('bad.docx',b'broken')}).status_code==400
        assert client.post('/api/documents',files={'file':('bad.txt',b'hello\x00binary')}).status_code==400
        content='测试独立设备的维护周期为二十三个月，维护时需要断开主电源。'.encode()
        response=client.post('/api/documents',files={'file':('new.txt',content,'text/plain')})
        assert response.status_code==202
        identifier=response.json()['id']
        for _ in range(100):
            row=next(r for r in client.get('/api/documents').json() if r['id']==identifier)
            if row['status'] in {'ready','error'}:break
            time.sleep(.04)
        assert row['status']=='ready',row
        assert row['file_type']=='txt' and row['quality_report']['characters']>10
        assert client.get(f'/api/documents/{identifier}/file').content==content
        assert client.get(f'/api/documents/{identifier}/chunks?page=1').json()


def test_image_and_scanned_pdf_ocr(tmp_path,monkeypatch):
    monkeypatch.setattr(ocr,'recognize',lambda image:'本地识别的维护说明：额定电压24 V，维护前断开主电源。'*8)
    from app import documents
    monkeypatch.setattr(documents,'recognize',ocr.recognize)
    image=Image.new('RGB',(600,800),'white')
    png=tmp_path/'scan.png';image.save(png)
    _,chunks,report=parse_document(png,'png')
    assert report['ocr_pages']==[1] and '24 V' in chunks[0]['content']
    pdf=tmp_path/'scan.pdf';image.save(pdf,'PDF')
    _,chunks,report=parse_document(pdf,'pdf')
    assert report['ocr_pages']==[1] and chunks[0]['kind']=='ocr'


def test_parse_quality_identifies_blank_and_garbled():
    report=quality_report(3,[{'page':1,'content':'乱码\ufffd'*30},{'page':2,'content':'短页'}],'pdf')
    assert report['status']=='warning' and report['empty_pages']==[3]
    assert report['garbled_pages']==[1] and report['sparse_pages']==[2]


def test_profiles_hide_keys_and_reject_duplicates(monkeypatch):
    monkeypatch.setenv('TEST_MODEL_SECRET','private-test-secret')
    value=[{'id':'second','label':'第二模型','base_url':'https://example.test/v1','model':'test-model','api_key_env':'TEST_MODEL_SECRET'}]
    monkeypatch.setenv('LLM_PROFILES',json.dumps(value))
    assert resolve_model('second')['api_key']=='private-test-secret'
    assert 'private-test-secret' not in str(public_models())
    assert public_models()[1]['ready']
    monkeypatch.setenv('LLM_PROFILES',json.dumps(value+value))
    with pytest.raises(ValueError):public_models()


def test_selected_model_routes_request(monkeypatch):
    monkeypatch.setenv('TEST_MODEL_SECRET','secret')
    monkeypatch.setenv('LLM_PROFILES',json.dumps([{'id':'other','base_url':'https://example.test/v1','model':'other-model','api_key_env':'TEST_MODEL_SECRET'}]))
    original=httpx.AsyncClient
    def handle(request):
        assert request.url.host=='example.test'
        assert json.loads(request.content)['model']=='other-model'
        return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"测试回答[1]"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    async def run():return ''.join([x async for x in providers.stream_answer('问题','资料',[],model_id='other')])
    assert asyncio.run(run())=='测试回答[1]'


def test_publication_uses_standard_question_without_removed_fields():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()['id']
        payload={**body(chunk),'aliases':['多久维护一次？'],'conditions':'旧客户端遗留字段'}
        result=client.post(f'/api/feedback/{fid}/publish',json=payload)
        assert result.status_code==200,result.text
        assert 'aliases' not in result.json() and 'conditions' not in result.json()
        assert exact_approved_answer('多久维护一次',[doc]) is None
        assert exact_approved_answer(payload['question'],[doc])['answer']==payload['answer']
        schema=client.get('/openapi.json').json()['components']['schemas']['PublishInput']['properties']
        assert 'aliases' not in schema and 'conditions' not in schema
        assert client.get('/api/knowledge/conflicts').status_code==404


def test_batch_withdraw_is_atomic_and_audited():
    with TestClient(app) as client:
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'incorrect'}).json()['id']
        row=client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).json()
        batch={'items':[{'id':row['id'],'revision':1},{'id':'missing','revision':1}],'reason':'来源需核对'}
        assert client.post('/api/knowledge/approved/batch-withdraw',json=batch).status_code==409
        assert exact_approved_answer(body(chunk)['question'],[doc])
        batch['items']=batch['items'][:1]
        assert client.post('/api/knowledge/approved/batch-withdraw',json=batch).json()['updated']==1
        assert not exact_approved_answer(body(chunk)['question'],[doc])
        assert client.get(f'/api/feedback/{fid}').json()['status']=='in_progress'
        assert client.get(f'/api/feedback/{fid}/publication-history').json()[0]['action']=='withdraw'


def test_insights_and_batch_close_preserve_published():
    with TestClient(app) as client:
        identifiers=[]
        with Session() as session:
            c=Conversation(title='聚合测试',document_ids=[]);session.add(c);session.flush()
            for _ in range(2):
                m=Message(conversation_id=c.id,role='assistant',content='原文',request_context={'question':'独立聚合设备如何复位？','document_ids':[]});session.add(m);session.flush();identifiers.append(m.id)
            session.commit()
        ids=[client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'missing'}).json()['id'] for mid in identifiers]
        groups=client.get('/api/feedback-insights').json()['groups']
        assert any(set(ids).issubset(g['ids']) for g in groups)
        assert client.post('/api/feedback-batch-close',json={'ids':ids,'reason':'  '}).status_code==422
        assert client.post('/api/feedback-batch-close',json={'ids':ids,'reason':'重复问题已核对'}).json()['updated']==2
        assert all(client.get(f'/api/feedback/{fid}').json()['status']=='resolved' for fid in ids)


def test_answer_policy_keeps_refusal_and_source_validation():
    assert finalize_answer('### 需要澄清\n\n请说明具体型号。',[])[2]=='clarification'
    answer='已知结果[1]\n\n'+'没有引用的长篇实质性建议。'*10
    assert finalize_answer(answer,[{'id':1,'document_name':'资料','page':1}])[2]=='no_evidence'



def test_short_scanned_pdf_keeps_valid_ocr_text(tmp_path,monkeypatch):
    monkeypatch.setattr(ocr,'recognize',lambda image:'设备工作电压为24V，维护前断电。')
    path=tmp_path/'short.pdf';Image.new('RGB',(200,300),'white').save(path,'PDF')
    _,chunks,report=parse_document(path,'pdf')
    assert chunks and report['ocr_pages']==[1]


def test_generated_non_pdf_reference_uses_logical_location():
    answer,citations,mode,_=finalize_answer('设备工作电压为24V。[1]',[{'id':1,'document_name':'参数表.xlsx','file_type':'xlsx','page':2,'heading':'电气参数 · 行 101–200'}])
    assert mode=='generated' and '第 2 节' in answer and 'PDF 第' not in answer



def test_same_question_across_documents_uses_normal_retrieval(monkeypatch):
    from app import main
    with TestClient(app) as client:
        for _ in range(2):
            _,mid,chunk=seed()
            fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'problem','category':'missing'}).json()['id']
            assert client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).status_code==200
        calls=[]
        async def normal_retrieval(*args,**kwargs):
            calls.append(args)
            return [],'keyword',''
        monkeypatch.setattr(main,'retrieve',normal_retrieval)
        response=client.post('/api/chat',json={'question':body('unused')['question']})
        assert calls and '需要澄清' not in response.text and 'event: error' not in response.text



def test_removed_metadata_is_preserved_in_legacy_database():
    from sqlalchemy import inspect,text
    from app.db import engine
    with TestClient(app) as client:
        columns={column['name'] for column in inspect(engine).get_columns('published_knowledge')}
        with engine.begin() as connection:
            if 'aliases' not in columns:connection.execute(text("ALTER TABLE published_knowledge ADD COLUMN aliases JSON NOT NULL DEFAULT '[]'"))
            if 'conditions' not in columns:connection.execute(text("ALTER TABLE published_knowledge ADD COLUMN conditions TEXT NOT NULL DEFAULT ''"))
        doc,mid,chunk=seed()
        fid=client.put(f'/api/messages/{mid}/feedback',json={'rating':'helpful'}).json()['id']
        published=client.post(f'/api/feedback/{fid}/publish',json=body(chunk)).json()
        legacy_aliases=json.dumps(['旧的人工同义问法'])
        with engine.begin() as connection:
            connection.execute(text('UPDATE published_knowledge SET aliases=:aliases, conditions=:conditions WHERE id=:id'),{'aliases':legacy_aliases,'conditions':'旧的适用条件','id':published['id']})
        updated=client.post(f'/api/feedback/{fid}/publish',json=body(chunk,1))
        assert updated.status_code==200 and 'aliases' not in updated.json() and 'conditions' not in updated.json()
        assert exact_approved_answer('旧的人工同义问法',[doc]) is None
        assert exact_approved_answer(body(chunk)['question'],[doc])['answer']==body(chunk)['answer']
        with engine.connect() as connection:
            saved=connection.execute(text('SELECT aliases,conditions FROM published_knowledge WHERE id=:id'),{'id':published['id']}).one()
            assert saved[0]==legacy_aliases and saved[1]=='旧的适用条件'
