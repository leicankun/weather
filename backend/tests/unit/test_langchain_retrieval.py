"""验证实际 LangChain 组件、并行召回、Document 元数据与 LCEL 可组合性。"""

import asyncio
from typing import Any

import pytest
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from pydantic import Field

from app.services.hybrid_retrieval import reciprocal_rank_fusion
from app.services.langchain_retrieval import (
    DashScopeRerankCompressor,
    ScopedBM25Retriever,
    ScoredEnsembleRetriever,
    as_document,
    as_hit,
)


def hit(key, **scores):
    return {
        "id": key,
        "title": "广州塔",
        "source": None,
        "content": "相同正文",
        "chunk_index": 0,
        **scores,
    }


def test_rrf确实委托原生Ensemble并按ID区分相同正文(monkeypatch):
    calls = []
    original = EnsembleRetriever.weighted_reciprocal_rank

    def wrapped(self, lists):
        calls.append(lists)
        return original(self, lists)

    monkeypatch.setattr(EnsembleRetriever, "weighted_reciprocal_rank", wrapped)
    dense = [hit(1, similarity=0.9), hit(1, similarity=0.9), hit(2, similarity=0.8)]
    sparse = [hit(2, bm25_score=2.0)]
    fused = reciprocal_rank_fusion(dense, sparse, k=60)
    assert len(calls) == 1 and [len(items) for items in calls[0]] == [2, 1]
    assert [item["id"] for item in fused] == [2, 1]
    assert fused[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert "rrf_score" not in dense[0]


def test_Document往返保留正文ID空来源及分数():
    row = hit(3, similarity=None, bm25_score=0.25, rerank_score=0.0)
    doc = as_document(row)
    assert isinstance(doc, Document) and doc.id == "3"
    assert as_hit(doc) == row
    assert "content" not in doc.metadata


class BarrierRetriever(BaseRetriever):
    docs: list[Document]
    ready: Any = Field(exclude=True)
    peer: Any = Field(exclude=True)

    def _get_relevant_documents(self, query, *, run_manager):
        raise NotImplementedError

    async def _aget_relevant_documents(self, query, *, run_manager):
        self.ready.set()
        await asyncio.wait_for(self.peer.wait(), timeout=2)
        return self.docs


class Recorder(AsyncCallbackHandler):
    def __init__(self):
        self.starts = []
        self.ends = []

    async def on_retriever_start(self, serialized, query, *, run_id, tags=None, **kwargs):
        self.starts.append((run_id, tags or []))

    async def on_retriever_end(self, documents, *, run_id, **kwargs):
        self.ends.append(run_id)


class ReverseReranker:
    def __init__(self):
        self.candidates = []

    async def rerank(self, query, candidates):
        self.candidates = candidates
        return [
            {**row, "rerank_score": 0.9, "rerank_rank": index}
            for index, row in enumerate(reversed(candidates), start=1)
        ]


async def test_原生Ensemble并行调用两路并传播回调再重排():
    a, b = asyncio.Event(), asyncio.Event()
    ensemble = ScoredEnsembleRetriever(
        retrievers=[
            BarrierRetriever(
                docs=[as_document(hit(1, similarity=0.9))], ready=a, peer=b, tags=["dense"]
            ),
            BarrierRetriever(
                docs=[as_document(hit(2, bm25_score=1.0))], ready=b, peer=a, tags=["sparse"]
            ),
        ],
        weights=[1.0, 1.0],
        c=60,
        id_key="id",
        tags=["rrf"],
    )
    client = ReverseReranker()
    retriever = ContextualCompressionRetriever(
        base_retriever=ensemble,
        base_compressor=DashScopeRerankCompressor(client=client, candidate_k=3, top_k=1),
        tags=["compression"],
    )
    recorder = Recorder()
    docs = await retriever.ainvoke("广州塔", config={"callbacks": [recorder]})
    assert [row["id"] for row in client.candidates] == [2, 1]
    assert [doc.metadata["id"] for doc in docs] == [1]
    assert docs[0].metadata["similarity"] == 0.9
    assert docs[0].metadata["rerank_score"] == 0.9
    assert len(recorder.starts) == len(recorder.ends) == 4
    assert {run_id for run_id, _ in recorder.starts} == set(recorder.ends)
    assert all(
        any(tag in tags for _, tags in recorder.starts)
        for tag in ("dense", "sparse", "rrf", "compression")
    )


async def test_BM25异步检索标题与查询重复词保持原分数():
    retriever = ScopedBM25Retriever(docs=[as_document(hit(1))], k=5)
    once = await retriever.ainvoke("广州塔")
    repeated = await retriever.ainvoke("广州塔 广州塔")
    assert once[0].metadata["bm25_score"] > 0
    assert repeated == once
    assert await retriever.ainvoke("完全不存在的编号XYZ0099") == []


async def test_Compressor也可接入标准LangChain问答链():
    retriever = ContextualCompressionRetriever(
        base_retriever=ScopedBM25Retriever(docs=[as_document(hit(1))]),
        base_compressor=DashScopeRerankCompressor(client=ReverseReranker(), candidate_k=3, top_k=1),
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "只依据资料回答，不足时说明无法确认。资料：{context}"),
            ("human", "{input}"),
        ]
    )
    combine = create_stuff_documents_chain(FakeListChatModel(responses=["资料回答"]), prompt)
    chain = create_retrieval_chain(retriever, combine)
    result = await chain.ainvoke({"input": "广州塔"})
    assert result["answer"] == "资料回答"
    assert result["context"][0].page_content == "相同正文"
    assert result["context"][0].metadata["rerank_score"] == 0.9


def test_异步重排适配拒绝隐式跨事件循环同步调用():
    compressor = DashScopeRerankCompressor(client=ReverseReranker(), candidate_k=3, top_k=1)
    with pytest.raises(NotImplementedError, match="ainvoke"):
        compressor.compress_documents([], "广州塔")
