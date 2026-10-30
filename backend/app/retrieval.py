import asyncio
import json
import math
import re
from collections import Counter
from functools import lru_cache

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, bindparam, func, select, text, type_coerce
from sqlalchemy.orm import load_only

from .config import embedding_signature
from .db import Chunk, Document, Session, engine
from .providers import ProviderError, embed
from .text import GENERIC, focus_words, normalize, tokens, words

_epoch = 0


def invalidate_cache():
    global _epoch
    _epoch += 1
    _corpus.cache_clear()
    _stats.cache_clear()


@lru_cache(maxsize=16)
def _corpus(document_ids, epoch):
    with Session() as session:
        query=select(Chunk,Document).join(Document).where(Document.status=='ready',Chunk.revision==Document.active_revision).options(load_only(Chunk.id,Chunk.document_id,Chunk.page,Chunk.ordinal,Chunk.heading,Chunk.content,Chunk.kind,Chunk.context,Chunk.group_key,Chunk.search_terms,Chunk.revision))
        if document_ids:
            query=query.where(Document.id.in_(document_ids))
        return tuple(session.execute(query).all())


@lru_cache(maxsize=16)
def _stats(contents):
    counts=[Counter(tokens(value)) for value in contents]
    df=Counter(term for counter in counts for term in counter)
    lengths=[sum(counter.values()) for counter in counts]
    return counts,df,lengths,sum(lengths)/max(1,len(lengths)) or 1


def lexical_rank(query,chunks):
    terms=set(tokens(query))
    if not terms or not chunks:
        return []
    counts,df,lengths,avg=_stats(tuple((c.heading or '')+' '+c.content for c in chunks))
    focus=focus_words(query)
    phrases=[word for word in words(query) if word not in GENERIC and len(word)>2]
    result=[]
    for index,counter in enumerate(counts):
        value=normalize((chunks[index].heading or '')+' '+chunks[index].content)
        matched={term for term in terms if counter[term]}
        focus_hits=sum(word in value for word in focus)
        if not matched or (focus and not focus_hits):
            continue
        score=0
        for term in matched:
            tf=counter[term]
            idf=math.log(1+(len(chunks)-df[term]+.5)/(df[term]+.5))
            score+=idf*tf*2.5/(tf+1.5*(.25+.75*lengths[index]/avg))
        coverage=len(matched)/len(terms)
        if coverage<.12:
            continue
        score*=1+.35*sum(phrase in value for phrase in phrases)+.3*focus_hits/max(1,len(focus))
        if re.search(r'排查|故障|失败|无反应|不能正常|不正常',normalize(query)) and (re.search(r'故障|排查|排除',chunks[index].heading or '') or '采取的措施' in value):
            score*=1.8
        result.append((index,score))
    return sorted(result,key=lambda row:row[1],reverse=True)


def _fts_candidates(question,document_ids):
    terms=list(dict.fromkeys(tokens(question)))[:70]
    if not terms:
        return []
    with Session() as session:
        if engine.dialect.name=='sqlite':
            expression=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
            params={'query':expression}
            scope=''
            if document_ids:
                names=[]
                for i,value in enumerate(document_ids):
                    name=f'd{i}';names.append(':'+name);params[name]=value
                scope=' AND document_id IN ('+','.join(names)+')'
            rows=session.execute(text('SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH :query'+scope+' ORDER BY bm25(chunk_fts) LIMIT 80'),params)
            return [row[0] for row in rows]
        expression=' | '.join("'"+term.replace("'","''")+"'" for term in terms)
        query=func.to_tsquery('simple',expression)
        score=func.ts_rank_cd(func.to_tsvector('simple',Chunk.search_terms),query)
        statement=select(Chunk.id).join(Document).where(Document.status=='ready',Chunk.revision==Document.active_revision,func.to_tsvector('simple',Chunk.search_terms).op('@@')(query))
        if document_ids:
            statement=statement.where(Document.id.in_(document_ids))
        return list(session.scalars(statement.order_by(score.desc()).limit(80)))


def _vector_candidates(vector,document_ids,signature):
    with Session() as session:
        if engine.dialect.name=='postgresql':
            distance=type_coerce(Chunk.embedding.op('<=>')(bindparam('query_vector',value=vector,type_=Vector())),Float)
            similarity=1-distance
            conditions=[func.vector_dims(Chunk.embedding)==len(vector)]
        else:
            similarity=func.vector_cosine(Chunk.embedding,json.dumps(vector))
            conditions=[]
        statement=select(Chunk.id,similarity.label('similarity')).join(Document).where(Document.status=='ready',Chunk.revision==Document.active_revision,Document.embedding_signature==signature,Chunk.embedding.is_not(None),*conditions)
        if document_ids:
            statement=statement.where(Document.id.in_(document_ids))
        return list(session.execute(statement.order_by(similarity.desc()).limit(32)))


async def _retrieve_documents(question: str,document_ids: list[str],limit=6,allow_remote=True):
    scope=tuple(sorted(set(document_ids)))
    rows=await asyncio.to_thread(_corpus,scope,_epoch)
    if not rows:
        return [],'keyword',''
    chunks=[r[0] for r in rows]
    query_codes=set(re.findall(r'[a-z][a-z0-9]*(?:[_.-][a-z0-9]+)*',normalize(question)))
    query_codes-={'what','how','which','when','does','can','is','are','the','of','to','in','for','and','or','with','on','at','why','tell','me','please','support','supported'}
    if query_codes:
        vocabulary=await asyncio.to_thread(_stats,tuple((c.heading or '')+' '+c.content for c in chunks))
        if any(len(code)>=3 and code not in vocabulary[1] for code in query_codes):
            return [],'keyword','资料未出现问题中的型号或协议名称，请核对名称后重试。'
    fts_ids=set(await asyncio.to_thread(_fts_candidates,question,scope))
    # 兼容尚未升级的旧库；现代索引从FTS候选进入本地词法重排。
    indices=[i for i,c in enumerate(chunks) if c.id in fts_ids] if fts_ids else list(range(len(chunks)))
    local=await asyncio.to_thread(lexical_rank,question,[chunks[i] for i in indices])
    lexical=[(indices[i],score) for i,score in local[:40] if score >= local[0][1]*.45]
    fusion={i:1.5/(60+rank) for rank,(i,_) in enumerate(lexical,1)}
    mode,warning='keyword',''
    signature=embedding_signature()
    eligible=signature and any(doc.embedding_signature==signature for _,doc in rows)
    if allow_remote and eligible:
        try:
            vector=(await embed([question]))[0]
            vector_hits=await asyncio.to_thread(_vector_candidates,vector,scope,signature)
            lookup={chunk.id:i for i,chunk in enumerate(chunks)}
            focus=focus_words(question)
            for rank,(cid,similarity) in enumerate(vector_hits,1):
                if cid not in lookup or similarity is None:
                    continue
                index=lookup[cid]
                shared=any(term in normalize(chunks[index].content) for term in focus)
                if similarity>=.45 and (shared or similarity>=.78):
                    fusion[index]=fusion.get(index,0)+1/(60+rank)
            mode='hybrid'
        except ProviderError as exc:
            warning=str(exc)+' 已降级为关键词检索。'
    elif signature and not eligible:
        warning='当前文档尚无匹配的向量索引，使用关键词检索。'
    selected,seen,total=[],set(),0
    for index in sorted(fusion,key=lambda i:fusion[i],reverse=True):
        chunk,doc=rows[index]
        key=(doc.id,chunk.group_key or chunk.id)
        if key in seen:
            continue
        context=chunk.context or chunk.content
        if total+len(context)>7000:
            continue
        seen.add(key);total+=len(context)
        selected.append({'id':len(selected)+1,'chunk_id':chunk.id,'document_id':doc.id,'document_name':doc.name,'page':chunk.page,'heading':chunk.heading,'content':context,'matched_content':chunk.content,'kind':chunk.kind,'revision':doc.active_revision,'score':round(fusion[index],5)})
        if len(selected)>=limit:
            break
    return selected,mode,warning


async def retrieve(question: str, document_ids: list[str], limit=6, allow_remote=True, include_approved=True):
    from .publication import retrieve_approved
    approved = await asyncio.to_thread(retrieve_approved, question, document_ids, limit) if include_approved else []
    hits,mode,warning = await _retrieve_documents(question,document_ids,limit,allow_remote)
    approved_chunks={hit['chunk_id'] for hit in approved}
    combined=approved+[hit for hit in hits if hit['chunk_id'] not in approved_chunks]
    result=[]
    total=0
    for hit in combined:
        if len(result)>=limit or total+len(hit['content'])>10000:
            continue
        total+=len(hit['content'])
        result.append(dict(hit,id=len(result)+1))
    return result, mode, warning
