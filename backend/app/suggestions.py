"""Local document-grounded starter questions; no provider calls."""
import re
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .db import Chunk, Document, Session

router = APIRouter(prefix='/api')


class SuggestionRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=100)


def topic(heading):
    value = re.sub(r'^\s*(?:第[一二三四五六七八九十百0-9]+[章节]\s*|\d+(?:[.．]\d+)*[、.．\s]*)', '', heading).strip()
    value = re.sub(r'^(?:\s*\d+(?:[.．]\d+)*[.、．]?\s*)+', '', value)
    value = re.sub(r'\s*[（(][^()（）]*页码[^()（）]*[)）]', '', value).strip()
    if value in {'文档正文', '正文内容', '正文', '目录', '概述', '产品概述'}:
        return None
    if not 3 <= len(value) <= 38 or re.search(r'目录|版权|版本历史|修订记录|用户手册|使用手册|^表\s*\d|^图\s*\d', value):
        return None
    return value


@lru_cache(maxsize=64)
def build_questions(versions):
    groups = []
    with Session() as session:
        for document_id, revision, name in versions:
            rows = session.execute(select(Chunk.heading, func.min(Chunk.ordinal), func.min(Chunk.page)).where(
                Chunk.document_id == document_id, Chunk.revision == revision,
            ).group_by(Chunk.heading).order_by(func.min(Chunk.ordinal)))
            candidates, seen = [], set()
            for heading, _, page in rows:
                label = topic(heading)
                if not label or label in seen:
                    continue
                seen.add(label)
                question = f'{label}有哪些说明？'
                kind = 'overview'
                if re.search(r'设置|操作|安装|使用|连接|调节|充电', label):
                    kind = 'guide'
                elif re.search(r'故障|报警|警告|维护|保养', label):
                    kind = 'troubleshoot'
                elif re.search(r'参数|规格|性能|尺寸|技术', label):
                    kind = 'parameter'
                candidates.append({'text': question, 'kind': kind, 'document_id': document_id,
                                   'document_name': name, 'page': page, 'topic': label})
            if not candidates:
                # 没有有效标题时使用真实正文中的短句，避免推荐其他产品的问题。
                contents = session.scalars(select(Chunk.content).where(Chunk.document_id == document_id, Chunk.revision == revision).order_by(Chunk.ordinal).limit(20))
                for content in contents:
                    line = next((x.strip() for x in re.split(r'[。；\n]', content) if 5 <= len(x.strip()) <= 30), None)
                    if line:
                        candidates.append({'text': f'文档中“{line}”的具体说明是什么？', 'kind': 'overview',
                                           'document_id': document_id, 'document_name': name, 'page': None, 'topic': line})
                        break
            groups.append(candidates)
    result = []
    # 多文档轮流取题，避免“全部文档”只推荐第一份文档。
    for index in range(4):
        for candidates in groups:
            if index < len(candidates):
                result.append(candidates[index])
                if len(result) == 4:
                    return tuple(result)
    return tuple(result)


@router.post('/suggestions')
def suggestions(body: SuggestionRequest):
    with Session() as session:
        query = select(Document).where(Document.status == 'ready')
        if body.document_ids:
            query = query.where(Document.id.in_(body.document_ids))
        docs = list(session.scalars(query.order_by(Document.created_at, Document.id)))
        versions = tuple((d.id, d.active_revision, d.name) for d in docs)
    return {'items': list(build_questions(versions)), 'mode': 'local',
            'note': '根据当前文档标题和正文推荐，不调用模型。'}
