"""Evaluate local retrieval only; never calls a model provider."""
import argparse
import asyncio
import json
import sys
import time
import unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.db import init_db
from app.retrieval import retrieve


def normalize(value):
    return ''.join(unicodedata.normalize('NFKC',value).lower().split())


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--questions',type=Path,default=ROOT/'output/review/questions.json')
    parser.add_argument('--output',type=Path,default=ROOT/'output/upgrade/evaluation.json')
    args=parser.parse_args()
    init_db()
    questions=json.loads(args.questions.read_text())
    results=[]
    for question in questions:
        start=time.perf_counter()
        hits,mode,warning=await retrieve(question['question'],[],allow_remote=False)
        pages=question['pages']
        evidence=question['evidence']
        results.append(dict(id=question['id'],question=question['question'],expected_pages=pages,pages=[h['page'] for h in hits],hit1=bool(hits and hits[0]['page'] in pages),hit3=any(h['page'] in pages for h in hits[:3]),hit6=any(h['page'] in pages for h in hits),evidence_complete=bool(hits) and all(normalize(e) in normalize('\n'.join(h['heading']+'\n'+h['content'] for h in hits)) for e in evidence),rejected=not hits,ms=round((time.perf_counter()-start)*1000,2),hits=hits))
    answerable=[r for r in results if r['expected_pages']]
    negatives=[r for r in results if not r['expected_pages']]
    summary={'answerable':len(answerable),'hit1':sum(r['hit1'] for r in answerable),'hit3':sum(r['hit3'] for r in answerable),'hit6':sum(r['hit6'] for r in answerable),'evidence_complete6':sum(r['evidence_complete'] for r in answerable),'negative_count':len(negatives),'negative_rejected':sum(r['rejected'] for r in negatives)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False))
    for row in results:
        print(row['id'],row['pages'],row['hit1'],row['evidence_complete'],row['rejected'])


if __name__=='__main__':
    asyncio.run(main())
