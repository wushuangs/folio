"""Export local feedback questions as unlabeled evaluation candidates."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

from sqlalchemy import select
from app.db import AnswerFeedback, Document, Session, init_db


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'output/quality/feedback-candidates.json')
    args=parser.parse_args()
    init_db()
    questions=[]
    with Session() as session:
        rows=session.scalars(select(AnswerFeedback).where(AnswerFeedback.rating=='problem')
                             .order_by(AnswerFeedback.created_at)).all()
        ready={doc.id:doc for doc in session.scalars(select(Document).where(Document.status=='ready'))}
        for row in rows:
            snapshot=row.snapshot or {}
            question=(snapshot.get('question') or '').strip()
            document_ids=list(dict.fromkeys(snapshot.get('document_ids') or []))
            if not question or any(identifier not in ready for identifier in document_ids):
                continue
            questions.append({
                'id':'feedback-'+row.id,
                'question':question,
                'category':row.category or '用户反馈',
                'document_ids':document_ids,
                'document_names':[ready[identifier].name for identifier in document_ids],
                'history':snapshot.get('history') or [],
                'pages':None,
                'expected_chunk_ids':[],
                'evidence':[],
            })
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(questions,ensure_ascii=False,indent=2))
    print(f'已导出 {len(questions)} 道待标注问题：{args.output}')
    print('标注时将 pages 设为预期页码列表；确认资料外问题则设为空列表。多文档问题请填写 expected_chunk_ids。')


if __name__=='__main__':
    main()
