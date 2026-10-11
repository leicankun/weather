"""公共入库和个人上传实际走共享标题分块器（真实 PostgreSQL，模型 mock）。"""

from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.knowledge import KnowledgeChunk
from app.models.user_knowledge import UserKnowledge
from app.services import rag_service
from app.services.embedding_client import embedding_client

DOCUMENT = "# 气候\n天气晴朗。\n## 交通\n地铁出行。\n### 餐饮\n推荐早茶。"
SECTIONS = ["# 气候\n天气晴朗。", "## 交通\n地铁出行。", "### 餐饮\n推荐早茶。"]


def mock_embeddings(monkeypatch):
    async def embed(texts):
        return [[1.0, *([0.0] * 1023)] for _ in texts]

    mock = AsyncMock(side_effect=embed)
    monkeypatch.setattr(embedding_client, "embed_texts", mock)
    return mock


async def test_上传个人文档按标题入库并能检索删除(client, auth_headers, db, monkeypatch):
    embed = mock_embeddings(monkeypatch)
    response = await client.post(
        "/api/v1/my-knowledge", headers=auth_headers, json={"title": "指南", "content": DOCUMENT}
    )
    assert response.status_code == 201
    assert response.json()["data"]["chunks"] == 3
    rows = (await db.scalars(select(UserKnowledge).order_by(UserKnowledge.chunk_index))).all()
    assert [r.content for r in rows] == SECTIONS
    assert [r.chunk_index for r in rows] == [0, 1, 2]
    assert embed.await_args.args[0] == SECTIONS
    response = await client.post(
        "/api/v1/my-knowledge/search", headers=auth_headers, json={"query": "早茶", "top_k": 3}
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert any(hit["content"] == SECTIONS[2] for hit in items)
    assert all(hit["content"] in SECTIONS for hit in items)
    response = await client.delete("/api/v1/my-knowledge/指南", headers=auth_headers)
    assert response.status_code == 200 and response.json()["data"]["deleted"] == 3


async def test_公共索引按标题向量化和入库(db, tmp_path, monkeypatch):
    from app.core.config import settings

    (tmp_path / "guide.md").write_text(DOCUMENT, encoding="utf-8")
    monkeypatch.setattr(settings, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "EMBED_CHUNK_SIZE", 300)
    monkeypatch.setattr(settings, "EMBED_CHUNK_OVERLAP", 50)
    monkeypatch.setattr(
        rag_service, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False)
    )
    embed = mock_embeddings(monkeypatch)
    result = await rag_service.build_index()
    assert result["documents"] == 1 and result["stored"] == result["chunks"] == 3
    rows = (await db.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index))).all()
    assert [r.content for r in rows] == SECTIONS
    assert all(r.source == "guide.md" and r.title == "气候" for r in rows)
    assert [text for call in embed.await_args_list for text in call.args[0]] == SECTIONS
