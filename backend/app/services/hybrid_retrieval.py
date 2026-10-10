"""BM25 + pgvector 双路召回 → RRF → 百炼重排序；两路严格共用检索范围。"""

from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from typing import Any

import jieba
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeChunk
from app.models.memory import MemoryEpisode
from app.models.user_knowledge import UserKnowledge
from app.services.embedding_client import embedding_client
from app.services.rerank_client import rerank_client

# 搜索模式兼顾中文词语、复合词与字母数字编号。
_tokenizer = jieba.Tokenizer()
_STOP_WORDS = frozenset(
    {"的", "了", "和", "是", "在", "我", "有", "吗", "呢", "请", "the", "a", "an"}
)


@lru_cache(maxsize=4096)
def tokenize(text: str) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", text).lower()
    return tuple(
        token
        for token in _tokenizer.cut_for_search(text, HMM=False)
        if re.fullmatch(r"[\w]+", token) and token not in _STOP_WORDS
    )


def bm25_recall(
    query: str, documents: list[dict[str, Any]], limit: int, *, k1: float, b: float
) -> list[dict[str, Any]]:
    """真实 BM25：词频饱和、逆文档频率与文档长度归一化。

    使用 Lucene 的正 IDF 形式，单文档/常见词仍能获得正分；零匹配不召回。
    文档频率只在调用方提供的语料范围内统计，私有语料不混入其他用户。
    """
    query_terms = set(tokenize(query))
    if not query_terms or not documents:
        return []
    counters = [Counter(tokenize(f"{doc['title']}\n{doc['content']}")) for doc in documents]
    lengths = [sum(counter.values()) for counter in counters]
    avg_length = sum(lengths) / len(documents)
    if not avg_length:
        return []
    frequencies = Counter(term for counter in counters for term in query_terms if term in counter)
    results = []
    for document, counter, length in zip(documents, counters, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            frequency = counter.get(term, 0)
            if not frequency:
                continue
            idf = math.log1p((len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            score += (
                idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * length / avg_length))
            )
        if score > 0:
            results.append({**document, "bm25_score": score})
    return sorted(results, key=lambda item: (-item["bm25_score"], item["id"]))[:limit]


def reciprocal_rank_fusion(
    dense: list[dict[str, Any]], sparse: list[dict[str, Any]], *, k: int
) -> list[dict[str, Any]]:
    """按片段 ID 去重，score = Σ 1 / (k + rank)，排名从 1 开始。"""
    if k < 1:
        raise ValueError("RRF k 必须为正整数")
    fused: dict[int, dict[str, Any]] = {}
    for name, items in (("dense", dense), ("bm25", sparse)):
        seen = set()
        rank = 0
        for item in items:
            key = item["id"]
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            result = fused.setdefault(
                key,
                {
                    **item,
                    "similarity": None,
                    "bm25_score": 0.0,
                    "rrf_score": 0.0,
                    "dense_rank": None,
                    "bm25_rank": None,
                },
            )
            if name == "dense":
                result["similarity"] = item["similarity"]
            else:
                result["bm25_score"] = item["bm25_score"]
            result[f"{name}_rank"] = rank
            result["rrf_score"] += 1 / (k + rank)
    # 同分时优先字面命中的片段，再按主键保持稳定顺序。
    return sorted(
        fused.values(),
        key=lambda item: (-item["rrf_score"], item["bm25_rank"] or math.inf, item["id"]),
    )


async def search_hybrid(
    db: AsyncSession,
    query: str,
    top_k: int,
    *,
    user_id: int | None = None,
    history: bool = False,
    exclude_message_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """公共库或指定用户私有库检索，不保存独立索引，新增/删除立即可见。"""
    query = query.strip()
    if not query:
        raise ValueError("查询文本不能为空")
    top_k = max(1, min(10, int(top_k)))
    candidate_k = max(top_k, settings.RAG_CANDIDATE_K)
    if history and user_id is None:
        raise ValueError("历史记忆检索必须指定用户")
    model = MemoryEpisode if history else (KnowledgeChunk if user_id is None else UserKnowledge)
    scope = [] if user_id is None else [model.user_id == user_id]
    if history and exclude_message_ids:
        scope.append(model.message_id.not_in(exclude_message_ids))
    columns = (model.id, model.title, model.source, model.content, model.chunk_index)
    rows = await db.execute(select(*columns).where(*scope).order_by(model.id))
    documents = [dict(row) for row in rows.mappings()]
    if not documents:
        return []

    # CPU 分词与 BM25 放到线程中，避免阻塞 FastAPI 事件循环。
    sparse_task = asyncio.to_thread(
        bm25_recall,
        query,
        documents,
        candidate_k,
        k1=settings.RAG_BM25_K1,
        b=settings.RAG_BM25_B,
    )
    vectors, sparse = await asyncio.gather(embedding_client.embed_texts([query]), sparse_task)
    vector = vectors[0]
    dense = []
    # 本地兜底对短文本可能生成零向量，余弦距离此时无定义。
    if vector and all(math.isfinite(value) for value in vector) and any(vector):
        distance = model.embedding.cosine_distance(vector)
        rows = await db.execute(
            select(*columns, (1 - distance).label("similarity"))
            .where(*scope, model.embedding.is_not(None))
            .order_by(distance, model.id)
            .limit(candidate_k)
        )
        dense = [dict(row) for row in rows.mappings() if math.isfinite(float(row["similarity"]))]

    fused = reciprocal_rank_fusion(dense, sparse, k=settings.RAG_RRF_K)
    candidates = fused[: max(top_k, settings.RERANK_CANDIDATE_K)]
    fused = (await rerank_client.rerank(query, candidates))[:top_k]
    for item in fused:
        item["rrf_score"] = round(item["rrf_score"], 8)
        item["bm25_score"] = round(item["bm25_score"], 6)
        if item["similarity"] is not None:
            item["similarity"] = round(float(item["similarity"]), 4)
    return fused
