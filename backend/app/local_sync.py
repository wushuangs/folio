import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .config import DATA, UPLOADS, local_sync_root
from .db import Document, KnowledgeApp, Session, SyncBinding, now, sync_fts, uid
from .documents import FORMATS, validate_file
from .ingestion import enqueue
from .retrieval import invalidate_cache

router=APIRouter(prefix='/api')
MAX_FILES=500
MAX_FILE_SIZE=50*1024*1024


def sync_root():
    root=local_sync_root()
    if root is None:
        raise HTTPException(409,'请先在 .env.local 配置 LOCAL_SYNC_DIR 并重启后端。')
    if not root.is_dir():
        raise HTTPException(409,'本地同步目录不存在或不可读取。')
    if root.is_relative_to(DATA) or DATA.is_relative_to(root):
        raise HTTPException(409,'同步目录不能包含 Folio 数据目录，请选择独立文件夹。')
    return root


def safe_source(root,relative_path):
    if '\x00' in relative_path:
        raise HTTPException(400,'文件路径无效。')
    value=Path(relative_path)
    if value.is_absolute() or not value.parts or '..' in value.parts or value.suffix.lower() not in FORMATS:
        raise HTTPException(400,'文件路径或格式无效。')
    try:
        source=(root/value).resolve(strict=True)
    except (OSError,RuntimeError):
        raise HTTPException(404,'源文件不存在。') from None
    if not source.is_relative_to(root) or not source.is_file():
        raise HTTPException(400,'只能同步指定目录内的普通文件。')
    if source.relative_to(root).as_posix()!=relative_path:
        raise HTTPException(400,'请使用预览中显示的文件路径。')
    return source


def digest_file(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while part:=stream.read(1024*1024):
            digest.update(part)
    return digest.hexdigest()


def scan(root):
    files=[]
    for path in root.rglob('*'):
        if path.suffix.lower() not in FORMATS or not path.is_file():
            continue
        if len(files)>=MAX_FILES:
            raise HTTPException(409,f'目录中超过 {MAX_FILES} 个受支持文件，请缩小同步范围。')
        relative=path.relative_to(root).as_posix()
        source=safe_source(root,relative)
        size=source.stat().st_size
        files.append((relative,size,digest_file(source) if size<=MAX_FILE_SIZE else None))
    return files


@router.get('/sync/preview')
def sync_preview():
    root=local_sync_root()
    if root is None:
        return {'configured':False,'root':None,'items':[]}
    root=sync_root()
    found=scan(root)
    items=[]
    seen=set()
    with Session() as session:
        bindings={row.relative_path:row for row in session.scalars(select(SyncBinding))}
        for relative,size,digest in found:
            seen.add(relative)
            binding=bindings.get(relative)
            kind=('too_large' if digest is None else
                  'indexing' if binding and binding.pending_document_id else
                  'new' if not binding else
                  'unchanged' if binding.observed_sha256==digest else 'changed')
            items.append({'relative_path':relative,'size':size,'sha256':digest,
                          'status':kind,'document_id':binding.document_id if binding else None,
                          'can_rollback':bool(binding and binding.previous_document_id and not binding.pending_document_id)})
        for relative,binding in bindings.items():
            if relative not in seen:
                items.append({'relative_path':relative,'size':None,'sha256':None,
                              'status':'missing','document_id':binding.document_id,
                              'can_rollback':bool(binding.previous_document_id and not binding.pending_document_id)})
    return {'configured':True,'root':str(root),'items':sorted(items,key=lambda item:item['relative_path'])}


def finalize_pending(session,new_document_id,success):
    binding=session.scalar(select(SyncBinding).where(
        SyncBinding.pending_document_id==new_document_id).with_for_update())
    if not binding:
        return False
    if not success:
        if binding.document_id is None:
            session.delete(binding)
        else:
            binding.pending_document_id=None
        return False
    document=session.get(Document,new_document_id)
    old_id=binding.document_id
    if not document or document.status!='ready':
        raise ValueError('待同步文档尚未完成索引。')
    if old_id and old_id!=new_document_id and binding.owned:
        other=session.scalar(select(func.count()).select_from(SyncBinding).where(
            SyncBinding.document_id==old_id,SyncBinding.relative_path!=binding.relative_path))
        if not other:
            previous=session.get(Document,old_id)
            if previous and previous.status=='ready':
                previous.status='archived'
                session.flush()
                sync_fts(session,old_id)
    if old_id and old_id!=new_document_id:
        replace_app_document(session,old_id,new_document_id)
    binding.previous_document_id=old_id if old_id!=new_document_id else binding.previous_document_id
    binding.previous_owned=binding.owned
    binding.document_id=new_document_id
    binding.observed_sha256=document.sha256
    binding.owned=binding.pending_owned
    binding.pending_document_id=None
    binding.updated_at=now()
    return True


def replace_app_document(session,old_id,new_id):
    for application in session.scalars(select(KnowledgeApp)):
        if old_id in application.document_ids:
            application.document_ids=list(dict.fromkeys(
                new_id if document_id==old_id else document_id
                for document_id in application.document_ids))
            application.updated_at=now()


class SyncApplyInput(BaseModel):
    relative_path:str=Field(min_length=1,max_length=1000)
    expected_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/sync/apply')
def sync_apply(body:SyncApplyInput):
    root=sync_root()
    source=safe_source(root,body.relative_path)
    size=source.stat().st_size
    if size>MAX_FILE_SIZE:
        raise HTTPException(413,'源文件超过 50 MB。')
    digest=digest_file(source)
    if digest!=body.expected_sha256:
        raise HTTPException(409,'源文件在预览后发生变化，请重新扫描。')
    suffix=source.suffix.lower()
    staged_id=uid()
    target=UPLOADS/f'{staged_id}{suffix}'
    temporary=UPLOADS/f'{staged_id}{suffix}.tmp'
    with Session() as session:
        binding=session.get(SyncBinding,body.relative_path)
        if binding and binding.pending_document_id:
            raise HTTPException(409,'该文件正在建立索引，请等待完成。')
        if binding and binding.observed_sha256==digest:
            return {'status':'unchanged','document_id':binding.document_id}
        existing=session.scalar(select(Document).where(Document.sha256==digest))
        if existing and existing.status=='archived':
            raise HTTPException(409,'相同内容的文档已归档，请先在知识库恢复。')
    created=False
    if not existing:
        try:
            shutil.copyfile(source,temporary)
            if digest_file(temporary)!=digest:
                raise HTTPException(409,'源文件在复制时发生变化，请重新扫描。')
            validate_file(temporary,suffix)
            temporary.replace(target)
        except ValueError as exc:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise HTTPException(400,str(exc)) from exc
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        created=True
    try:
        with Session() as session:
            binding=session.get(SyncBinding,body.relative_path)
            if binding and binding.pending_document_id:
                raise HTTPException(409,'该文件正在建立索引，请等待完成。')
            if created:
                document=Document(id=staged_id,name=source.name[:250],sha256=digest,size=size,file_type=suffix[1:])
                session.add(document)
            else:
                document=session.get(Document,existing.id)
            if not binding:
                binding=SyncBinding(relative_path=body.relative_path,document_id=None,
                                    observed_sha256='',owned=False)
                session.add(binding)
            binding.pending_document_id=document.id
            binding.pending_owned=created
            session.flush()
            if document.status=='ready':
                finalize_pending(session,document.id,True)
                session.commit()
                invalidate_cache()
                return {'status':'ready','document_id':document.id}
            session.commit()
        try:
            enqueue(document.id)
        except ValueError as exc:
            with Session() as session:
                finalize_pending(session,document.id,False)
                row=session.get(Document,document.id)
                if row and row.status=='queued':
                    row.status,row.indexing_status,row.error='error','error',str(exc)
                session.commit()
            raise HTTPException(409,str(exc)) from exc
        return {'status':'queued','document_id':document.id}
    except Exception:
        if created:
            with Session() as session:
                if not session.get(Document,staged_id):
                    target.unlink(missing_ok=True)
        raise


class SyncRollbackInput(BaseModel):
    relative_path:str=Field(min_length=1,max_length=1000)


@router.post('/sync/rollback')
def sync_rollback(body:SyncRollbackInput):
    sync_root()
    with Session() as session:
        binding=session.get(SyncBinding,body.relative_path)
        if not binding or not binding.previous_document_id:
            raise HTTPException(404,'没有可回退的同步版本。')
        if binding.pending_document_id:
            raise HTTPException(409,'文件正在同步，请等待完成。')
        current=session.get(Document,binding.document_id)
        previous=session.get(Document,binding.previous_document_id)
        if not current or not previous or previous.chunks==0:
            raise HTTPException(409,'旧版本的文档或索引已不可用。')
        if current.indexing_status in {'queued','processing'} or previous.indexing_status in {'queued','processing'}:
            raise HTTPException(409,'文档正在重建索引，请等待完成。')
        current_id,previous_id=current.id,previous.id
        other=session.scalar(select(func.count()).select_from(SyncBinding).where(
            SyncBinding.document_id==current_id,SyncBinding.relative_path!=binding.relative_path))
        if binding.owned and not other:
            current.status='archived'
        previous.status='ready'
        binding.document_id,binding.previous_document_id=previous_id,current_id
        binding.owned,binding.previous_owned=binding.previous_owned,binding.owned
        binding.observed_sha256=previous.sha256
        binding.updated_at=now()
        replace_app_document(session,current_id,previous_id)
        session.flush()
        sync_fts(session,current_id)
        sync_fts(session,previous_id)
        session.commit()
    invalidate_cache()
    return {'status':'ready','document_id':previous_id}
