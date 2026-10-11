"""真实数据库验证 LangChain 混合检索的空库及历史排除范围。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_classic.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_community.retrievers import BM25Retriever

from app.models.chat_message import ChatMessage
from app.models.knowledge import KnowledgeChunk
from app.models.memory import MemoryEpisode
from app.models.user import User
from app.services.langchain_retrieval import (
    ScopedPGVectorRetriever,
    build_hybrid_retriever,
)


def clients():
    embedder = SimpleNamespace(embed_texts=AsyncMock(return_value=[[1.0, *([0.0] * 1023)]]))

    async def passthrough(query, candidates):
        return [{**row, "rerank_score": None, "rerank_rank": None} for row in candidates]

    reranker = SimpleNamespace(rerank=AsyncMock(side_effect=passthrough))
    return embedder, reranker


async def test_构建真实LangChain组件空库不调用外部模型(db):
    embedder, reranker = clients()
    retriever = await build_hybrid_retriever(db, 5, embedder=embedder, reranker=reranker)
    assert isinstance(retriever, ContextualCompressionRetriever)
    assert isinstance(retriever.base_retriever, EnsembleRetriever)
    dense, sparse = retriever.base_retriever.retrievers
    assert isinstance(dense, ScopedPGVectorRetriever) and isinstance(sparse, BM25Retriever)
    assert await retriever.ainvoke("广州塔") == []
    embedder.embed_texts.assert_not_awaited()
    reranker.rerank.assert_not_awaited()
    with pytest.raises(NotImplementedError, match="ainvoke"):
        dense.invoke("广州塔")


async def test_历史排除消息与用户范围作用于两路及重排(db):
    embedder, reranker = clients()
    users = [User(username=name, password_hash="unused") for name in ("lc_owner", "lc_other")]
    db.add_all(users)
    await db.flush()
    episodes = []
    for user in [users[0], users[0], users[1]]:
        message = ChatMessage(user_id=user.id, conversation_id="old", role="user", content="暗号")
        db.add(message)
        await db.flush()
        episode = MemoryEpisode(
            user_id=user.id,
            message_id=message.id,
            conversation_id="old",
            title="暗号",
            source="history",
            content=f"暗号{message.id}",
            chunk_index=0,
            embedding=[1.0, *([0.0] * 1023)],
        )
        db.add(episode)
        episodes.append(episode)
    await db.commit()
    retriever = await build_hybrid_retriever(
        db,
        5,
        embedder=embedder,
        reranker=reranker,
        user_id=users[0].id,
        history=True,
        exclude_message_ids=[episodes[0].message_id],
    )
    docs = await retriever.ainvoke("暗号")
    assert [doc.metadata["id"] for doc in docs] == [episodes[1].id]
    assert docs[0].metadata["dense_rank"] == docs[0].metadata["bm25_rank"] == 1
    candidates = reranker.rerank.await_args.args[1]
    assert [row["id"] for row in candidates] == [episodes[1].id]


@pytest.mark.parametrize("vector", [[], [float("nan")], [0.0] * 1024])
async def test_无有效查询向量仍通过LangChain返回BM25(db, vector):
    embedder, reranker = clients()
    embedder.embed_texts.return_value = [vector]
    db.add(KnowledgeChunk(title="广州塔", source=None, content="门票价格"))
    await db.commit()
    retriever = await build_hybrid_retriever(db, 1, embedder=embedder, reranker=reranker)
    docs = await retriever.ainvoke("广州塔")
    assert len(docs) == 1 and docs[0].metadata["dense_rank"] is None
    assert docs[0].metadata["bm25_rank"] == 1
