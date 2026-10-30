"""Import bundled PDF manuals without requiring an HTTP upload."""
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import UPLOADS
from app.db import Document, Session, init_db, uid
from app.ingestion import ingest
from sqlalchemy import select

async def main():
    init_db()
    for path in (ROOT / 'downloads').glob('*.pdf'):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with Session() as session:
            existing = session.scalar(select(Document).where(Document.sha256 == digest))
            if existing and existing.status == 'ready':
                print(f'已存在：{existing.name}（{existing.pages} 页）')
                continue
            if existing:
                document_id = existing.id
            else:
                document_id = uid()
                session.add(Document(id=document_id, name=path.name, sha256=digest, size=path.stat().st_size))
                session.commit()
            shutil.copyfile(path, UPLOADS / f'{document_id}.pdf')
        await ingest(document_id)
        with Session() as session:
            doc = session.get(Document, document_id)
            print(f'{doc.status}: {doc.name} / {doc.pages} 页 / {doc.chunks} 片段 {doc.error}')
            if doc.status != 'ready':
                raise SystemExit(1)

if __name__ == '__main__':
    asyncio.run(main())
