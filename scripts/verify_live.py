"""Opt-in real provider smoke test, isolated from user documents and conversations."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))


def main():
    with tempfile.TemporaryDirectory(prefix='folio-live-') as directory:
        os.environ['DATA_DIR'] = directory
        os.environ['DATABASE_URL'] = f'sqlite:///{directory}/test.db'
        from app.config import settings
        from app.main import app
        from fastapi.testclient import TestClient
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        config = settings()
        report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'isolated': True,
                  'models': {'generation': config['llm_model'], 'embedding': config['embedding_model']},
                  'steps': {}, 'passed': False}
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 50 700 Td (Folio calibration unit warranty period is 17 months.) Tj 0 -24 Td (This document is a synthetic service test fixture.) Tj 0 -24 Td (The warranty starts on the purchase date.) Tj 0 -24 Td (Contact a human technician when the answer is not documented.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        pdf = BytesIO()
        writer.write(pdf)
        try:
            if not config['llm_key'] or not config['embedding_key']:
                raise RuntimeError('missing_provider_configuration')
            with TestClient(app) as client:
                response = client.post('/api/documents', files={'file': ('folio-live-fixture.pdf', pdf.getvalue(), 'application/pdf')})
                response.raise_for_status()
                doc_id = response.json()['id']
                report['steps']['upload'] = True
                deadline = time.monotonic()+120
                while time.monotonic() < deadline:
                    doc = next(d for d in client.get('/api/documents').json() if d['id'] == doc_id)
                    if doc['status'] in ['ready', 'error'] and doc['indexing_status'] not in ['queued', 'processing']:
                        break
                    time.sleep(1)
                report['steps']['index'] = {'status': doc['status'], 'vector_ready': doc['vector_ready'], 'error': doc['error'], 'chunks': doc['chunks']}
                response = client.post('/api/chat', json={'question': 'Folio calibration unit warranty period?', 'document_ids': [doc_id]})
                response.raise_for_status()
                events = []
                for packet in response.text.split('\n\n'):
                    lines = packet.splitlines()
                    event = next((x[7:] for x in lines if x.startswith('event: ')), '')
                    data = next((json.loads(x[6:]) for x in lines if x.startswith('data: ')), None)
                    if event and data is not None:
                        events.append((event, data))
                start = next((d for e,d in events if e == 'start'), {})
                sources = next((d for e,d in events if e == 'sources'), {})
                done = next((d for e,d in events if e == 'done'), {})
                failure = next((d for e,d in events if e == 'error'), {})
                messages = client.get(f'/api/conversations/{start["conversation_id"]}/messages').json() if start else []
                answer = next((m for m in reversed(messages) if m['role'] == 'assistant'), {})
                report['steps']['chat'] = {'retrieval_mode': sources.get('retrieval_mode'), 'citation_count': len(sources.get('citations', [])),
                                           'delta_received': any(e == 'delta' for e,d in events), 'done': bool(done),
                                           'error': failure.get('message'), 'warning': sources.get('warning'),
                                           'answer_mode': answer.get('mode'), 'answer': answer.get('content'),
                                           'message_persisted': answer.get('id') == done.get('message_id') if done else False}
                report['passed'] = bool(doc['vector_ready'] and sources.get('retrieval_mode') == 'hybrid' and done and answer.get('mode') == 'generated' and '17' in answer.get('content', '') and report['steps']['chat']['message_persisted'])
        except Exception as exc:
            report['error_type'] = type(exc).__name__
        output = ROOT/'output/quality'/f'live-smoke-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(output)
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
