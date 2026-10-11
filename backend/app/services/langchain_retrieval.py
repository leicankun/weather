"""LangChain 编排：BM25 / pgvector → Ensemble RRF → DashScope compressor。

适配已有业务表与分数契约，每个实例绑定一次请求的会话和语料范围。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from itertools import groupby
from typing import Any

import jieba
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.callbacks.manager import Callbacks
from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from langchain_core.retrievers import BaseRetriever
from pydantic import Field
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeChunk
from app.models.memory import MemoryEpisode
from app.models.user_knowledge import UserKnowledge

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


def as_document(hit: dict[str, Any]) -> Document:
    return Document(
        id=str(hit["id"]),
        page_content=hit["content"],
        metadata={key: value for key, value in hit.items() if key != "content"},
    )


def as_hit(document: Document) -> dict[str, Any]:
    return {**document.metadata, "content": document.page_content}


class LuceneBM25(BM25Okapi):
    """沿用正 IDF；词频饱和与长度归一化由 rank-bm25 实现。"""

    def _calc_idf(self, nd: dict[str, int]) -> None:
        self.idf = {
            word: math.log1p((self.corpus_size - frequency + 0.5) / (frequency + 0.5))
            for word, frequency in nd.items()
        }


class ScopedBM25Retriever(BM25Retriever):
    """保留标题索引、中文分词、正 IDF、零匹配过滤及原始分数。"""

    k1: float = 1.2
    b: float = 0.75

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        terms = sorted(set(tokenize(query)))
        if not terms or not self.docs:
            return []
        corpus = [tokenize(f"{doc.metadata['title']}\n{doc.page_content}") for doc in self.docs]
        if not any(corpus):
            return []
        # BaseRetriever 默认在线程池执行同步召回，分词和评分不阻塞事件循环。
        vectorizer = LuceneBM25(corpus, k1=self.k1, b=self.b)
        scores = vectorizer.get_scores(terms)
        hits = [
            Document(
                id=doc.id,
                page_content=doc.page_content,
                metadata={**doc.metadata, "bm25_score": float(score)},
            )
            for doc, score in zip(self.docs, scores, strict=True)
            if score > 0
        ]
        return sorted(hits, key=lambda doc: (-doc.metadata["bm25_score"], doc.metadata["id"]))[
            : self.k
        ]


class ScopedPGVectorRetriever(BaseRetriever):
    """在现有 SQLAlchemy + pgvector 表上实现 LangChain Retriever 接口。"""

    db: AsyncSession = Field(exclude=True, repr=False)
    model: Any = Field(exclude=True, repr=False)
    scope: list[Any] = Field(exclude=True, repr=False)
    columns: tuple[Any, ...] = Field(exclude=True, repr=False)
    embedder: Any = Field(exclude=True, repr=False)
    k: int
    has_documents: bool = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        raise NotImplementedError("数据库检索请使用 await retriever.ainvoke(query)")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        if not self.has_documents:
            return []
        vectors = await self.embedder.embed_texts([query])
        vector = vectors[0] if vectors else []
        if not vector or not all(math.isfinite(value) for value in vector) or not any(vector):
            return []
        distance = self.model.embedding.cosine_distance(vector)
        rows = await self.db.execute(
            select(*self.columns, (1 - distance).label("similarity"))
            .where(*self.scope, self.model.embedding.is_not(None))
            .order_by(distance, self.model.id)
            .limit(self.k)
        )
        return [
            as_document(dict(row))
            for row in rows.mappings()
            if math.isfinite(float(row["similarity"]))
        ]


class ScoredEnsembleRetriever(EnsembleRetriever):
    """使用原生 RRF 排序，补齐两路分数，并保持旧接口的同分顺序。"""

    def weighted_reciprocal_rank(self, doc_lists: list[list[Document]]) -> list[Document]:
        if self.c < 1:
            raise ValueError("RRF k 必须为正整数")
        if len(doc_lists) != 2 or len(self.weights) != 2:
            raise ValueError("混合检索需要 Dense 和 BM25 两路")
        unique_lists = []
        metadata: dict[int, dict[str, Any]] = {}
        for name, docs, weight in zip(("dense", "bm25"), doc_lists, self.weights, strict=True):
            seen = set()
            unique = []
            for doc in docs:
                key = doc.metadata["id"]
                if key in seen:
                    continue
                seen.add(key)
                unique.append(doc)
                rank = len(unique)
                entry = metadata.setdefault(
                    key,
                    {
                        **doc.metadata,
                        "similarity": None,
                        "bm25_score": 0.0,
                        "rrf_score": 0.0,
                        "dense_rank": None,
                        "bm25_rank": None,
                    },
                )
                field = "similarity" if name == "dense" else "bm25_score"
                entry[field] = doc.metadata[field]
                entry[f"{name}_rank"] = rank
                # 原生 Ensemble 不暴露分数，此处只补充接口元数据。
                entry["rrf_score"] += weight / (self.c + rank)
            unique_lists.append(unique)
        ranked = super().weighted_reciprocal_rank(unique_lists)
        # 原生算法决定分数组顺序；只在同分组内按旧契约稳定排序。
        results = []
        for _, tied in groupby(ranked, key=lambda doc: metadata[doc.metadata["id"]]["rrf_score"]):
            results.extend(
                sorted(
                    tied,
                    key=lambda doc: (
                        metadata[doc.metadata["id"]]["bm25_rank"] or math.inf,
                        doc.metadata["id"],
                    ),
                )
            )
        return [
            Document(
                id=doc.id, page_content=doc.page_content, metadata=metadata[doc.metadata["id"]]
            )
            for doc in results
        ]


class DashScopeRerankCompressor(BaseDocumentCompressor):
    """ContextualCompressionRetriever 的异步百炼适配，保留客户端故障降级。"""

    client: Any = Field(exclude=True, repr=False)
    candidate_k: int
    top_k: int

    def compress_documents(
        self, documents: Sequence[Document], query: str, callbacks: Callbacks | None = None
    ) -> Sequence[Document]:
        raise NotImplementedError("模型重排请使用 await retriever.ainvoke(query)")

    async def acompress_documents(
        self, documents: Sequence[Document], query: str, callbacks: Callbacks | None = None
    ) -> Sequence[Document]:
        candidates = [as_hit(doc) for doc in documents[: max(self.top_k, self.candidate_k)]]
        ranked = await self.client.rerank(query, candidates)
        return [as_document(hit) for hit in ranked[: self.top_k]]


async def build_hybrid_retriever(
    db: AsyncSession,
    top_k: int,
    *,
    embedder: Any,
    reranker: Any,
    user_id: int | None = None,
    history: bool = False,
    exclude_message_ids: list[int] | None = None,
) -> ContextualCompressionRetriever:
    """一次请求一个 Retriever；两路同范围，不跨请求缓存私有语料。"""
    if history and user_id is None:
        raise ValueError("历史记忆检索必须指定用户")
    top_k = max(1, min(10, int(top_k)))
    candidate_k = max(top_k, settings.RAG_CANDIDATE_K)
    model = MemoryEpisode if history else (KnowledgeChunk if user_id is None else UserKnowledge)
    scope = [] if user_id is None else [model.user_id == user_id]
    if history and exclude_message_ids:
        scope.append(model.message_id.not_in(exclude_message_ids))
    columns = (model.id, model.title, model.source, model.content, model.chunk_index)
    # 先完成语料读取，随后 Ensemble 并行执行 CPU BM25 和 Dense。
    # BM25 不访问会话，避免一个 AsyncSession 并发执行两个 SQL 查询。
    rows = await db.execute(select(*columns).where(*scope).order_by(model.id))
    documents = [as_document(dict(row)) for row in rows.mappings()]
    dense = ScopedPGVectorRetriever(
        db=db,
        model=model,
        scope=scope,
        columns=columns,
        embedder=embedder,
        k=candidate_k,
        has_documents=bool(documents),
        tags=["pgvector"],
    )
    sparse = ScopedBM25Retriever(
        docs=documents,
        k=candidate_k,
        k1=settings.RAG_BM25_K1,
        b=settings.RAG_BM25_B,
        tags=["bm25"],
    )
    ensemble = ScoredEnsembleRetriever(
        retrievers=[dense, sparse],
        weights=[1.0, 1.0],
        c=settings.RAG_RRF_K,
        id_key="id",
        tags=["rrf"],
    )
    return ContextualCompressionRetriever(
        base_retriever=ensemble,
        base_compressor=DashScopeRerankCompressor(
            client=reranker, candidate_k=settings.RERANK_CANDIDATE_K, top_k=top_k
        ),
        tags=["hybrid-rag"],
    )
