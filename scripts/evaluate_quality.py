"""Versioned retrieval / generation evaluation; local by default."""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from sqlalchemy import select
from app.config import settings
from app.db import Document, Session, init_db
from app.main import finalize_answer, rewrite_query
from app.providers import stream_answer
from app.retrieval import retrieve
from evaluate_local import normalize


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['local', 'live'], default='local')
    parser.add_argument('--questions', type=Path, default=ROOT/'evaluation/questions-v1.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    init_db()
    source = next((ROOT/'downloads').glob('*.pdf'))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with Session() as session:
        doc = session.scalar(select(Document).where(Document.sha256 == digest, Document.status == 'ready'))
        if not doc:
            raise SystemExit('请先导入并完成仓库示例手册索引。')
        manifest = {'name': doc.name, 'sha256': doc.sha256, 'revision': doc.active_revision,
                    'parser_version': doc.parser_version, 'vector_index': bool(doc.embedding_signature)}
        doc_id = doc.id
    config = settings()
    if args.mode == 'live' and not config['llm_key']:
        raise SystemExit('模型未配置，未执行真实评测。')
    raw = args.questions.read_bytes()
    questions = json.loads(raw)
    if args.limit:
        questions = questions[:args.limit]
    results = []
    for q in questions:
        started = time.perf_counter()
        row = {'id': q['id'], 'question': q['question'], 'category': q['category'],
               'expected_pages': q['pages'], 'human_review': {'status': 'pending' if args.mode == 'live' else 'not_generated', 'factually_correct': None, 'conditions_complete': None, 'no_fabrication': None, 'citations_support_claims': None, 'notes': ''}}
        try:
            async with asyncio.timeout(120):
                query = rewrite_query(q['question'], q.get('history', []))
                hits, mode, warning = await retrieve(query, [doc_id], allow_remote=args.mode == 'live')
                text = '\n'.join(h['heading']+'\n'+h['content'] for h in hits)
                row.update(query=query, hits=hits, retrieval_mode=mode, warning=warning,
                           hit1=bool(hits and hits[0]['page'] in q['pages']),
                           hit6=any(h['page'] in q['pages'] for h in hits),
                           evidence_complete=bool(hits) and all(normalize(e) in normalize(text) for e in q['evidence']),
                           rejected=not hits)
                if args.mode == 'live':
                    answer = ''
                    if hits:
                        context = '\n\n'.join(f'[{h["id"]}] {h["document_name"]}，PDF 第 {h["page"]} 页\n{h["content"]}' for h in hits)
                        async for delta in stream_answer(q['question'], context, q.get('history', [])):
                            answer += delta
                        answer, citations, answer_mode, extra = finalize_answer(answer, hits)
                        row.update(answer=answer, answer_mode=answer_mode, citation_ids_valid=answer_mode == 'generated', validation_warning=extra)
                    else:
                        row.update(answer='资料不足，未调用生成模型。', answer_mode='no_evidence', citation_ids_valid=None)
        except Exception as exc:
            from app.providers import ProviderError
            row['error'] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        row['elapsed_ms'] = round((time.perf_counter()-started)*1000)
        results.append(row)
        print(q['id'], row.get('retrieval_mode', 'error'), row.get('hit6', False), flush=True)
    positives = [r for r in results if r['expected_pages']]
    negatives = [r for r in results if not r['expected_pages']]
    summary = {'total': len(results), 'answerable': len(positives),
               'hit1': sum(r.get('hit1', False) for r in positives),
               'hit6': sum(r.get('hit6', False) for r in positives),
               'evidence_complete': sum(r.get('evidence_complete', False) for r in positives),
               'unanswerable': len(negatives), 'correct_rejection': sum(r.get('rejected', False) for r in negatives),
               'errors': sum('error' in r for r in results),
               'warnings': sum(bool(r.get('warning')) for r in results),
               'answer_accuracy': None, 'note': '答案正确率待人工审核，不能由检索命中率推断。'}
    output = args.output or ROOT/'output/quality'/f'{args.mode}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'mode': args.mode, 'questions_sha256': hashlib.sha256(raw).hexdigest(),
                                  'document': manifest, 'models': {'generation': config['llm_model'], 'embedding': config['embedding_model']},
                                  'summary': summary, 'results': results}, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    print(output)
    if summary['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
