"""真实 pgvector 查询与 BM25/RRF，外部 Embedding 使用可控替身。"""

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.knowledge import KnowledgeChunk
from app.models.user import User
from app.models.user_knowledge import UserKnowledge
from app.services import rag_service, user_knowledge_service
from app.services.embedding_client import embedding_client
from app.services.hybrid_retrieval import search_hybrid


def vector(first=1.0, second=0.0):
    return [first, second, *([0.0] * 1022)]


@pytest.fixture(autouse=True)
def local_embeddings(monkeypatch):
    async def embed(texts):
        return [vector() for _ in texts]

    monkeypatch.setattr(embedding_client, "embed_texts", embed)


async def test_双路候选融合去重并保留分数(db, monkeypatch):
    monkeypatch.setattr(settings, "RAG_CANDIDATE_K", 2)
    rows = [
        KnowledgeChunk(title="语义命中", source="a", content="室内活动", embedding=vector()),
        KnowledgeChunk(title="双路命中", source="b", content="门票", embedding=vector(0.8, 0.6)),
        KnowledgeChunk(title="字面命中", source="c", content="门票 门票", embedding=None),
    ]
    db.add_all(rows)
    await db.commit()
    results = await search_hybrid(db, "门票", 2)
    assert results[0]["id"] == rows[1].id
    assert len(results) == 2 and len({item["id"] for item in results}) == 2
    both = results[0]
    assert both["dense_rank"] == 2 and both["bm25_rank"] == 2
    assert both["rrf_score"] == pytest.approx(2 / 62, abs=1e-8)
    assert both["similarity"] == pytest.approx(0.8)
    sparse = results[1]
    assert sparse["id"] == rows[2].id
    assert sparse["similarity"] is None and sparse["dense_rank"] is None


async def test_编号字面召回补回向量遗漏并支持删除(db, monkeypatch):
    monkeypatch.setattr(settings, "RAG_CANDIDATE_K", 1)
    db.add(KnowledgeChunk(title="高向量相似度", source="a", content="普通旅行", embedding=vector()))
    target = KnowledgeChunk(title="票券", source="b", content="GZ2026ABC 门票核验", embedding=None)
    db.add(target)
    await db.commit()
    assert (await search_hybrid(db, "GZ2026ABC", 1))[0]["id"] == target.id
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id == target.id))
    await db.commit()
    assert target.id not in {item["id"] for item in await search_hybrid(db, "GZ2026ABC", 5)}


async def test_公共接口返回融合分数且只查公共资料(client, db):
    db.add(KnowledgeChunk(title="广州塔", source=None, content="广州塔门票", embedding=None))
    await db.commit()
    for response in (
        await client.get("/api/v1/knowledge/search", params={"q": "广州塔", "top_k": 1}),
        await client.post("/api/v1/knowledge/search", json={"query": "广州塔", "top_k": 1}),
    ):
        assert response.status_code == 200
        item = response.json()["data"]["items"][0]
        assert item["title"] == "广州塔" and item["similarity"] is None
        assert item["bm25_score"] > 0 and item["rrf_score"] == pytest.approx(1 / 61, abs=1e-8)


async def test_个人两路召回与词频统计均按用户隔离(client, auth_headers, db):
    owner_id = await db.scalar(select(User.id).where(User.username == "tester"))
    other = User(username="knowledge_other", password_hash="unused")
    db.add(other)
    await db.flush()
    mine = UserKnowledge(user_id=owner_id, title="我的门票", content="GZ2026ABC", embedding=None)
    db.add(mine)
    db.add(
        UserKnowledge(user_id=other.id, title="他人的门票", content="GZ2026ABC", embedding=vector())
    )
    db.add(
        KnowledgeChunk(title="公共门票", source="public", content="GZ2026ABC", embedding=vector())
    )
    await db.commit()
    response = await client.post(
        "/api/v1/my-knowledge/search", headers=auth_headers, json={"query": "GZ2026ABC"}
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["id"] for item in items] == [mine.id]
    # 单用户单文档，文档频率不会被他人的同词文档改变。
    assert items[0]["bm25_score"] == pytest.approx(0.287682, abs=1e-6)
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    response = await client.post(
        "/api/v1/my-knowledge/search", headers=other_headers, json={"query": "GZ2026ABC"}
    )
    assert [item["title"] for item in response.json()["data"]["items"]] == ["他人的门票"]
    response = await client.post("/api/v1/my-knowledge/search", json={"query": "GZ2026ABC"})
    assert response.status_code == 401
    assert [item["title"] for item in await rag_service.search("GZ2026ABC")] == ["公共门票"]


async def test_零向量和无向量时仍能关键词召回(db, monkeypatch):
    async def zero(texts):
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(embedding_client, "embed_texts", zero)
    db.add(KnowledgeChunk(title="雨", source="test", content="雨天室内活动", embedding=None))
    await db.commit()
    result = await search_hybrid(db, "雨", 5)
    assert len(result) == 1 and result[0]["dense_rank"] is None


async def test_空库与无关键词匹配返回空列表(db):
    assert await search_hybrid(db, "门票", 5) == []
    db.add(KnowledgeChunk(title="美食", source="test", content="早茶", embedding=None))
    await db.commit()
    assert await search_hybrid(db, "GZ2026ABC", 5) == []


@pytest.mark.parametrize("top_k", [0, -1, 11])
async def test_公共接口限制返回条数(client, top_k):
    assert (
        await client.get("/api/v1/knowledge/search", params={"q": "门票", "top_k": top_k})
    ).status_code == 422
    assert (
        await client.post("/api/v1/knowledge/search", json={"query": "门票", "top_k": top_k})
    ).status_code == 422


async def test_空查询和未指定用户不能检索私有库(db):
    with pytest.raises(ValueError):
        await search_hybrid(db, "  ", 5)
    with pytest.raises(ValueError):
        await user_knowledge_service.search(None, "门票", db=db)
