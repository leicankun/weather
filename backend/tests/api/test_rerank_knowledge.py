"""真实 PostgreSQL + FastAPI + 百炼 HTTP 替身，覆盖完整重排链路与用户隔离。"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.knowledge import KnowledgeChunk
from app.models.user import User
from app.models.user_knowledge import UserKnowledge
from app.services import hybrid_retrieval
from app.services.embedding_client import embedding_client
from app.services.rerank_client import RerankClient

URL = "https://rerank.test/api/v1/services/rerank/text-rerank/text-rerank"


@pytest.fixture(autouse=True)
def deterministic_clients(monkeypatch):
    async def zero_embeddings(texts):
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(embedding_client, "embed_texts", zero_embeddings)
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_K", 3)
    monkeypatch.setattr(settings, "RAG_CANDIDATE_K", 3)
    monkeypatch.setattr(
        hybrid_retrieval,
        "rerank_client",
        RerankClient(api_key="test-secret", base_url="https://rerank.test/api/v1"),
    )


def last_candidate_wins(request):
    payload = json.loads(request.content)
    count = len(payload["input"]["documents"])
    assert payload["parameters"]["top_n"] == count
    return httpx.Response(
        200,
        json={
            "output": {
                "results": [
                    {"index": index, "relevance_score": 0.95 if index == count - 1 else 0.1}
                    for index in range(count)
                ]
            }
        },
    )


async def test_公共GET和POST在最终topK前重排融合候选(client, db, respx_mock):
    for index, content in enumerate(["门票 门票 门票", "门票 门票", "门票"]):
        db.add(
            KnowledgeChunk(
                title=f"资料{index}",
                source="test",
                chunk_index=index,
                content=content,
                embedding=None,
            )
        )
    await db.commit()
    route = respx_mock.post(URL).mock(side_effect=last_candidate_wins)
    for response in (
        await client.get("/api/v1/knowledge/search", params={"q": "门票", "top_k": 1}),
        await client.post("/api/v1/knowledge/search", json={"query": "门票", "top_k": 1}),
    ):
        assert response.status_code == 200
        items = response.json()["data"]["items"]
        assert len(items) == 1 and items[0]["title"] == "资料2"
        assert items[0]["rerank_score"] == 0.95 and items[0]["rerank_rank"] == 1
        assert items[0]["bm25_rank"] == 3 and items[0]["rrf_score"] == pytest.approx(1 / 63)
        assert items[0]["similarity"] is None
    assert route.call_count == 2
    assert len(json.loads(route.calls.last.request.content)["input"]["documents"]) == 3


async def test_送入模型的私有候选按用户隔离且不含公共资料(client, auth_headers, db, respx_mock):
    owner_id = await db.scalar(select(User.id).where(User.username == "tester"))
    other = User(username="rerank_other", password_hash="unused")
    db.add(other)
    await db.flush()
    db.add_all(
        [
            UserKnowledge(user_id=owner_id, title="我的1", content="门票 门票"),
            UserKnowledge(user_id=owner_id, title="我的2", content="门票"),
            UserKnowledge(user_id=other.id, title="他人的资料", content="门票"),
            KnowledgeChunk(title="公共资料", source="test", content="门票"),
        ]
    )
    await db.commit()
    route = respx_mock.post(URL).mock(side_effect=last_candidate_wins)
    response = await client.post(
        "/api/v1/my-knowledge/search", headers=auth_headers, json={"query": "门票", "top_k": 1}
    )
    item = response.json()["data"]["items"][0]
    assert item["title"] == "我的2" and item["rerank_score"] == 0.95
    documents = json.loads(route.calls.last.request.content)["input"]["documents"]
    assert documents == ["我的1\n门票 门票", "我的2\n门票"]
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    response = await client.post(
        "/api/v1/my-knowledge/search", headers=other_headers, json={"query": "门票"}
    )
    assert [item["title"] for item in response.json()["data"]["items"]] == ["他人的资料"]
    assert json.loads(route.calls.last.request.content)["input"]["documents"] == [
        "他人的资料\n门票"
    ]
    assert (
        await client.post("/api/v1/my-knowledge/search", json={"query": "门票"})
    ).status_code == 401
    assert route.call_count == 2


async def test_模型失败时接口仍返回RRF结果和空重排分数(client, db, respx_mock):
    db.add(KnowledgeChunk(title="门票", source="test", content="门票", embedding=None))
    await db.commit()
    respx_mock.post(URL).mock(return_value=httpx.Response(503))
    response = await client.get("/api/v1/knowledge/search", params={"q": "门票"})
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["title"] == "门票" and item["rrf_score"] == pytest.approx(1 / 61)
    assert item["rerank_score"] is None and item["rerank_rank"] is None


async def test_空召回不调用模型且候选数不小于最终返回数(db, monkeypatch, respx_mock):
    assert await hybrid_retrieval.search_hybrid(db, "门票", 3) == []
    assert not respx_mock.calls
    monkeypatch.setattr(settings, "RERANK_CANDIDATE_K", 1)
    for index in range(3):
        db.add(
            KnowledgeChunk(
                title=f"资料{index}",
                source="test",
                chunk_index=index,
                content="门票",
                embedding=None,
            )
        )
    await db.commit()
    route = respx_mock.post(URL).mock(side_effect=last_candidate_wins)
    results = await hybrid_retrieval.search_hybrid(db, "门票", 3)
    assert len(results) == 3 and [item["rerank_rank"] for item in results] == [1, 2, 3]
    assert len(json.loads(route.calls.last.request.content)["input"]["documents"]) == 3
