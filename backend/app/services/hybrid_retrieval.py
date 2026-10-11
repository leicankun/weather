"""兼容原有入口与返回字段，实际检索由 LangChain Retriever 编排。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding_client import embedding_client
from app.services.langchain_retrieval import (
    ScopedBM25Retriever,
    ScoredEnsembleRetriever,
    as_document,
    as_hit,
    build_hybrid_retriever,
    tokenize,  # noqa: F401 — 保留原有分词函数的导入路径
)
from app.services.rerank_client import rerank_client


def bm25_recall(
    query: str, documents: list[dict[str, Any]], limit: int, *, k1: float, b: float
) -> list[dict[str, Any]]:
    """兼容入口，评分由 LangChain BM25 Retriever 执行。"""
    retriever = ScopedBM25Retriever(
        docs=[as_document(doc) for doc in documents], k=limit, k1=k1, b=b
    )
    return [as_hit(doc) for doc in retriever.invoke(query)]


def reciprocal_rank_fusion(
    dense: list[dict[str, Any]], sparse: list[dict[str, Any]], *, k: int
) -> list[dict[str, Any]]:
    """兼容入口复用 LangChain Ensemble 的融合实现。"""
    ensemble = ScoredEnsembleRetriever(
        retrievers=[ScopedBM25Retriever(docs=[]), ScopedBM25Retriever(docs=[])],
        weights=[1.0, 1.0],
        c=k,
        id_key="id",
    )
    documents = ensemble.weighted_reciprocal_rank(
        [[as_document(hit) for hit in dense], [as_document(hit) for hit in sparse]]
    )
    return [as_hit(doc) for doc in documents]


async def search_hybrid(
    db: AsyncSession,
    query: str,
    top_k: int,
    *,
    user_id: int | None = None,
    history: bool = False,
    exclude_message_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """公共/个人/历史共用 LangChain 两路召回 → RRF → reranker。"""
    query = query.strip()
    if not query:
        raise ValueError("查询文本不能为空")
    retriever = await build_hybrid_retriever(
        db,
        top_k,
        embedder=embedding_client,
        reranker=rerank_client,
        user_id=user_id,
        history=history,
        exclude_message_ids=exclude_message_ids,
    )
    hits = [as_hit(doc) for doc in await retriever.ainvoke(query)]
    for item in hits:
        item["rrf_score"] = round(item["rrf_score"], 8)
        item["bm25_score"] = round(item["bm25_score"], 6)
        if item["similarity"] is not None:
            item["similarity"] = round(float(item["similarity"]), 4)
    return hits
