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
from app.publication import exact_approved_answer
from app.providers import stream_answer
from app.retrieval import retrieve
from evaluate_local import normalize


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['local', 'live'], default='local')
    parser.add_argument('--questions', type=Path, default=ROOT/'evaluation/questions-v1.json')
    parser.add_argument('--reviews', type=Path, help='人工审核结果 JSON，按题目 ID 映射四项布尔结论')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    raw = args.questions.read_bytes()
    questions = json.loads(raw)
    if not isinstance(questions,list) or not questions:
        parser.error('questions must be a nonempty JSON array')
    if args.limit:
        questions = questions[:args.limit]
    reviews = json.loads(args.reviews.read_text()) if args.reviews else {}
    if not isinstance(reviews,dict):
        parser.error('reviews must be a JSON object keyed by question ID')
    init_db()
    needs_sample=any('document_ids' not in q for q in questions)
    source=next((ROOT/'downloads').glob('*.pdf'),None) if needs_sample else None
    if needs_sample and source is None:
        raise SystemExit('未指定文档范围，且仓库示例手册不存在。')
    digest=hashlib.sha256(source.read_bytes()).hexdigest() if source else None
    with Session() as session:
        doc=session.scalar(select(Document).where(Document.sha256 == digest, Document.status == 'ready')) if digest else None
        if needs_sample and not doc:
            raise SystemExit('请先导入并完成仓库示例手册索引。')
        default_ids=[doc.id] if doc else []
        requested={identifier for q in questions for identifier in q.get('document_ids',default_ids)}
        if any(not q.get('document_ids',default_ids) for q in questions):
            requested.update(session.scalars(select(Document.id).where(Document.status=='ready')))
        documents={row.id:row for row in session.scalars(select(Document).where(Document.id.in_(requested),Document.status=='ready'))} if requested else {}
        if set(documents)!=requested:
            raise SystemExit('评测题引用了不存在或尚未就绪的文档，请重新导出或更新文档范围。')
        manifests=[{'id':row.id,'name':row.name,'sha256':row.sha256,'revision':row.active_revision,
                    'parser_version':row.parser_version,'vector_index':bool(row.embedding_signature)}
                   for row in documents.values()]
    config = settings()
    if args.mode == 'live' and not config['llm_key']:
        raise SystemExit('模型未配置，未执行真实评测。')
    results = []
    for q in questions:
        started = time.perf_counter()
        document_ids=q.get('document_ids',default_ids)
        expected_pages=q.get('pages')
        expected_chunks=q.get('expected_chunk_ids',[])
        labeled=expected_pages is not None or bool(expected_chunks)
        positive=bool(expected_pages or expected_chunks)
        if expected_pages and len(document_ids)!=1 and not expected_chunks:
            raise SystemExit(f'{q["id"]}: 多文档题请标注 expected_chunk_ids，不能仅用页码判断命中。')
        review=reviews.get(q['id'],{})
        if not isinstance(review,dict):
            review={}
        review_fields=['factually_correct','conditions_complete','no_fabrication','citations_support_claims']
        reviewed=all(isinstance(review.get(key),bool) for key in review_fields)
        row = {'id': q['id'], 'question': q['question'], 'category': q['category'],
               'document_ids':document_ids,'expected_pages':expected_pages,
               'expected_chunk_ids':expected_chunks,'labeled':labeled,
               'human_review':{'status':('reviewed' if reviewed else 'pending') if args.mode=='live' else 'not_generated',
                               **{key:review.get(key) if reviewed else None for key in review_fields},
                               'notes':review.get('notes','')}}
        try:
            async with asyncio.timeout(120):
                query = rewrite_query(q['question'], q.get('history', []))
                exact=exact_approved_answer(q['question'],document_ids)
                hits,mode,warning=(exact['citations'],'approved','') if args.mode=='live' and exact else await retrieve(
                    query,document_ids,allow_remote=args.mode=='live')
                text = '\n'.join(h['heading']+'\n'+h['content'] for h in hits)
                rank=next((i for i,hit in enumerate(hits,1)
                           if hit['chunk_id'] in expected_chunks or
                           (expected_pages and hit['page'] in expected_pages and len(document_ids)==1)),None)
                row.update(query=query, answer_path='published_exact' if exact else 'retrieval',
                           hits=hits, retrieval_mode=mode, warning=warning,
                           first_relevant_rank=rank if positive else None,
                           hit1=rank==1 if positive else None,
                           hit3=bool(rank and rank<=3) if positive else None,
                           hit6=bool(rank and rank<=6) if positive else None,
                           evidence_complete=(bool(hits) and all(normalize(e) in normalize(text) for e in q.get('evidence',[]))) if positive else None,
                           rejected=not hits,context_characters=0 if args.mode=='live' and exact else sum(len(h['content']) for h in hits),
                           history_characters=sum(len(item.get('content','')) for item in q.get('history',[])),
                           provider_token_usage=None)
                if args.mode == 'live':
                    answer = ''
                    if exact:
                        answer=exact['answer']
                        row.update(answer=answer,answer_mode='approved',
                                   citation_ids_valid=True,answer_characters=len(answer),
                                   provider_token_usage=0)
                    elif hits:
                        context = '\n\n'.join(f'[{h["id"]}] {h["document_name"]}，PDF 第 {h["page"]} 页\n{h["content"]}' for h in hits)
                        async for delta in stream_answer(q['question'], context, q.get('history', [])):
                            answer += delta
                        answer, citations, answer_mode, extra = finalize_answer(answer, hits)
                        row.update(answer=answer, answer_mode=answer_mode, citation_ids_valid=answer_mode == 'generated', validation_warning=extra,
                                   answer_characters=len(answer))
                    else:
                        row.update(answer='资料不足，未调用生成模型。', answer_mode='no_evidence', citation_ids_valid=None,answer_characters=0)
        except Exception as exc:
            from app.providers import ProviderError
            row['error'] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        row['elapsed_ms'] = round((time.perf_counter()-started)*1000)
        results.append(row)
        print(q['id'], row.get('retrieval_mode', 'error'), row.get('hit6', False), flush=True)
    positives = [r for r in results if r['labeled'] and (r['expected_pages'] or r['expected_chunk_ids'])]
    negatives = [r for r in results if r['labeled'] and not (r['expected_pages'] or r['expected_chunk_ids'])]
    reviewed_rows=[r for r in results if r['human_review']['status']=='reviewed' and 'error' not in r]
    accurate=sum(all(r['human_review'][key] for key in review_fields) for r in reviewed_rows)
    summary = {'total': len(results), 'labeled':len(positives)+len(negatives),
               'unlabeled':len(results)-len(positives)-len(negatives),'answerable': len(positives),
               'hit1': sum(r.get('hit1', False) for r in positives),
               'hit3': sum(r.get('hit3', False) for r in positives),
               'hit6': sum(r.get('hit6', False) for r in positives),
               'mrr_at_6':round(sum(1/r['first_relevant_rank'] for r in positives if r.get('first_relevant_rank'))/len(positives),4) if positives else None,
               'evidence_complete': sum(r.get('evidence_complete', False) for r in positives),
               'unanswerable': len(negatives), 'correct_rejection': sum(r.get('rejected', False) for r in negatives),
               'errors': sum('error' in r for r in results),
               'warnings': sum(bool(r.get('warning')) for r in results),
               'answer_reviewed':len(reviewed_rows),
               'answer_accuracy':round(accurate/len(reviewed_rows),4) if reviewed_rows else None,
               'provider_token_usage':None,
               'note':'答案正确率只统计四项均经人工审核的题目。当前模型流未提供用量数据，报告仅记录上下文和回答字符数，不将其冒充 token。'}
    output = args.output or ROOT/'output/quality'/f'{args.mode}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'mode': args.mode, 'questions_sha256': hashlib.sha256(raw).hexdigest(),
                                  'documents': manifests, 'models': {'generation': config['llm_model'], 'embedding': config['embedding_model']},
                                  'summary': summary, 'results': results}, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    print(output)
    if summary['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
