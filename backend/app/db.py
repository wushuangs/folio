import json
import math
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default='')
    embedding_signature: Mapped[str] = mapped_column(Text, default='')
    active_revision: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    indexing_status: Mapped[str] = mapped_column(String(20), default='idle', server_default='idle')
    parser_version: Mapped[str] = mapped_column(String(30), default='legacy', server_default='legacy')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Chunk(Base):
    __tablename__ = 'chunks'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    page: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON().with_variant(Vector(), 'postgresql'), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default='0', index=True)
    kind: Mapped[str] = mapped_column(String(20), default='body', server_default='body')
    context: Mapped[str] = mapped_column(Text, default='', server_default='')
    group_key: Mapped[str] = mapped_column(String(80), default='', server_default='')
    search_terms: Mapped[str] = mapped_column(Text, default='', server_default='')


class Revision(Base):
    __tablename__ = 'index_revisions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int] = mapped_column(Integer)
    chunks: Mapped[int] = mapped_column(Integer)
    embedding_signature: Mapped[str] = mapped_column(Text, default='')
    parser_version: Mapped[str] = mapped_column(String(30), default='legacy')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IndexJob(Base):
    __tablename__ = 'index_jobs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    mode: Mapped[str] = mapped_column(String(16), default='full')
    status: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    error: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmbeddingCache(Base):
    __tablename__ = 'embedding_cache'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[list] = mapped_column(JSON)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='open')
    document_ids: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(24), default='')
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='complete', server_default='complete')
    retrieval_mode: Mapped[str] = mapped_column(String(20), default='', server_default='')
    warning: Mapped[str] = mapped_column(Text, default='', server_default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def vector_cosine(a, b):
    try:
        a, b = json.loads(a), json.loads(b)
        if len(a) != len(b):
            return -1.0
        norm = math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
        return sum(x*y for x,y in zip(a,b))/norm if norm else -1.0
    except (TypeError, ValueError):
        return -1.0


engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}, pool_pre_ping=True)
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def configure_sqlite(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=10000')
        connection.create_function('vector_cosine', 2, vector_cosine, deterministic=True)

Session = sessionmaker(engine, expire_on_commit=False)


def sync_fts(session, document_id):
    if engine.dialect.name != 'sqlite':
        return
    session.execute(text('DELETE FROM chunk_fts WHERE document_id=:id'), {'id': document_id})
    doc = session.get(Document, document_id)
    if not doc or doc.status == 'archived':
        return
    rows = session.query(Chunk).filter(Chunk.document_id == document_id, Chunk.revision == doc.active_revision).all()
    session.execute(text('INSERT INTO chunk_fts(chunk_id,document_id,terms) VALUES(:chunk_id,:document_id,:terms)'),
                    [{'chunk_id': c.id, 'document_id': document_id, 'terms': c.search_terms} for c in rows]) if rows else None


def init_db():
    if engine.dialect.name == 'postgresql':
        with engine.begin() as c:
            c.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    # 兼容已有本地数据库，只添加新字段，不删除原始记录或旧索引。
    additions = {
        'documents': {'active_revision': "INTEGER NOT NULL DEFAULT 0", 'indexing_status': "VARCHAR(20) NOT NULL DEFAULT 'idle'", 'parser_version': "VARCHAR(30) NOT NULL DEFAULT 'legacy'"},
        'chunks': {'revision': 'INTEGER NOT NULL DEFAULT 0', 'kind': "VARCHAR(20) NOT NULL DEFAULT 'body'", 'context': "TEXT NOT NULL DEFAULT ''", 'group_key': "VARCHAR(80) NOT NULL DEFAULT ''", 'search_terms': "TEXT NOT NULL DEFAULT ''"},
        'messages': {'status': "VARCHAR(20) NOT NULL DEFAULT 'complete'", 'retrieval_mode': "VARCHAR(20) NOT NULL DEFAULT ''", 'warning': "TEXT NOT NULL DEFAULT ''"},
        'conversations': {'document_ids': "JSON NOT NULL DEFAULT '[]'"},
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text('CREATE INDEX IF NOT EXISTS idx_chunk_revision ON chunks(document_id, revision)'))
        if engine.dialect.name == 'sqlite':
            c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, terms, tokenize='unicode61')"))
        else:
            c.execute(text("CREATE INDEX IF NOT EXISTS idx_chunk_terms ON chunks USING gin(to_tsvector('simple', search_terms))"))

    from .text import content_terms
    with Session() as session:
        for doc in session.scalars(select(Document)):
            if doc.chunks and not session.scalar(select(Revision).where(Revision.document_id==doc.id, Revision.revision==doc.active_revision)):
                session.add(Revision(document_id=doc.id,revision=doc.active_revision,pages=doc.pages,chunks=doc.chunks,embedding_signature=doc.embedding_signature,parser_version=doc.parser_version))
            dirty=False
            for chunk in session.scalars(select(Chunk).where(Chunk.document_id==doc.id,Chunk.revision==doc.active_revision,Chunk.search_terms=='')):
                chunk.search_terms=content_terms(chunk.heading+' '+chunk.content)
                dirty=True
            if dirty:
                session.flush()
                sync_fts(session,doc.id)
        session.commit()
