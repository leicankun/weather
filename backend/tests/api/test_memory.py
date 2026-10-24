"""真实 PostgreSQL/Agent 图验证双层记忆，外部模型替身可控制时序与故障。"""

import asyncio
import json

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy import func, select

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.memory import MemoryCard, MemoryEpisode
from app.models.user import User
from app.schemas.memory import CardExtraction, CardPatch
from app.services import agent, chat_history_service, hybrid_retrieval, memory_service, multi_agent
from app.services.embedding_client import _local_pseudo_vector, embedding_client
from app.services.rerank_client import RerankClient


def patch(value, evidence=None, key="diet"):
    return CardPatch(kind="preference", key=key, value=value, evidence=evidence or value)


class MemoryModel(GenericFakeChatModel):
    calls: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "独立的回答验证员" in messages[0].content:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(content='{"passed":true,"issues":[],"revised_answer":""}')
                    )
                ]
            )
        self.calls.append(list(messages))
        system = messages[0].content
        answer = "other" if "意图识别模块" in system else system
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


@pytest.fixture
def memory_enabled(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_ENABLED", True)

    async def embeddings(texts):
        return [_local_pseudo_vector(value, 1024) for value in texts]

    async def extract(prompt, schema, **kwargs):
        current = json.loads(prompt)["current_user_text"]
        cards = [patch("不吃辣")] if "不吃辣" in current else []
        if "长期住在天河区" in current:
            cards.append(
                CardPatch(
                    kind="fact", key="residence", value="常住天河区", evidence="长期住在天河区"
                )
            )
        return CardExtraction(cards=cards)

    monkeypatch.setattr(embedding_client, "embed_texts", embeddings)
    monkeypatch.setattr(memory_service, "ainvoke_json", extract)


@pytest.fixture
def memory_model(monkeypatch, memory_enabled):
    model = MemoryModel(messages=iter(()))
    multi_agent.reset_cache()
    agent._agent_instance = None
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)

    async def tools():
        return []

    async def domain_tools():
        return {}

    monkeypatch.setattr(agent, "_get_all_tools", tools)
    monkeypatch.setattr(multi_agent, "load_domain_tools", domain_tools)
    yield model
    multi_agent.reset_cache()
    agent._agent_instance = None


async def ask(client, path, text, conversation_id, headers):
    response = await client.post(
        path, headers=headers, json={"message": text, "conversation_id": conversation_id}
    )
    assert response.status_code == 200
    if path.endswith("stream"):
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
        return "".join(e.get("content", "") for e in events if e["type"] == "token")
    assert response.json()["code"] == 0
    return response.json()["data"]["answer"]


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_两个模式跨会话复用卡片并仅保存最终回答(
    client, auth_headers, db, memory_model, monkeypatch, mode, path
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    await ask(client, path, "我长期住在天河区，我不吃辣。", "first", auth_headers)
    # SSE done 后立即查卡片，不能存在后台写入竞态。
    cards = (await client.get("/api/v1/memory/cards", headers=auth_headers)).json()["data"]["items"]
    assert {card["key"] for card in cards} == {"diet", "residence"}
    answer = await ask(client, path, "结合我的情况说两句", "second", auth_headers)
    assert "不吃辣" in answer and "常住天河区" in answer
    # 多个领域和汇总节点接收相同的卡片；原始历史仍只写一轮。
    memory_model.calls.clear()
    await ask(client, path, "明天天气和穿什么", "second", auth_headers)
    generation = [call for call in memory_model.calls if "意图识别模块" not in call[0].content]
    assert generation and all("常住天河区" in call[0].content for call in generation)
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    assert (
        await db.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.user_id == owner)
        )
        == 6
    )
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 2


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_超出十条的旧消息通过历史RAG跨会话召回(
    client, auth_headers, db, memory_model, monkeypatch, mode, path
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    await chat_history_service.add_message(owner, "old", "user", "那次活动的暗号是蓝鲸7429")
    for index in range(12):
        await chat_history_service.add_message(owner, "old", "assistant", f"无关闲聊{index}")
    recent = await chat_history_service.get_recent_history(owner, "old")
    assert all("蓝鲸7429" not in message["content"] for message in recent)
    answer = await ask(client, path, "之前活动的暗号是什么", "new", auth_headers)
    assert "蓝鲸7429" in answer
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 0


async def test_证据用户角色隔离与同主题并发只保留新版(db, auth_headers, memory_enabled):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    older = await chat_history_service.add_message(owner, "a", "user", "不吃辣")
    newer = await chat_history_service.add_message(owner, "b", "user", "现在喜欢微辣")
    assistant = await chat_history_service.add_message(owner, "a", "assistant", "不吃辣")
    other = User(username="memory_other", password_hash="unused")
    db.add(other)
    await db.commit()
    assert await memory_service.apply_cards(owner, assistant["id"], [patch("不吃辣")]) == 0
    assert await memory_service.apply_cards(other.id, older["id"], [patch("不吃辣")]) == 0
    assert await memory_service.apply_cards(owner, older["id"], [patch("花生过敏")]) == 0
    await asyncio.gather(
        memory_service.apply_cards(owner, newer["id"], [patch("喜欢微辣", "现在喜欢微辣")]),
        memory_service.apply_cards(owner, older["id"], [patch("不吃辣")]),
    )
    # 再让慢提取结果最后到达，也不能倒退。
    await memory_service.apply_cards(owner, older["id"], [patch("不吃辣")])
    cards = await memory_service.list_cards(owner)
    assert len(cards) == 1 and cards[0]["data"]["value"] == "喜欢微辣"
    assert cards[0]["source_message_id"] == newer["id"]
    assert await memory_service.list_cards(other.id) == []


async def test_历史两路和reranker均严格按用户隔离(
    client, auth_headers, db, memory_enabled, monkeypatch, respx_mock
):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    other = User(username="secret_memory", password_hash="unused")
    db.add(other)
    await db.commit()
    await chat_history_service.add_message(owner, "one", "user", "自己的暗号蓝鲸7429")
    await chat_history_service.add_message(other.id, "two", "user", "秘密暗号雪豹9999")
    await memory_service.index_history(owner)
    await memory_service.index_history(other.id)
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(
        hybrid_retrieval,
        "rerank_client",
        RerankClient(api_key="test", base_url="https://rerank.test/api/v1"),
    )

    def reply(request):
        docs = json.loads(request.content)["input"]["documents"]
        assert len(docs) == 1 and "蓝鲸7429" in docs[0] and "雪豹9999" not in docs[0]
        return httpx.Response(
            200, json={"output": {"results": [{"index": 0, "relevance_score": 0.95}]}}
        )

    route = respx_mock.post(
        "https://rerank.test/api/v1/services/rerank/text-rerank/text-rerank"
    ).mock(side_effect=reply)
    response = await client.post(
        "/api/v1/memory/search", headers=auth_headers, json={"query": "暗号是什么"}
    )
    item = response.json()["data"]["items"][0]
    assert item["dense_rank"] == 1 and item["bm25_rank"] == 1 and item["rerank_score"] == 0.95
    assert route.call_count == 1
    assert (await client.get("/api/v1/memory/cards")).status_code == 401
    assert (await client.delete("/api/v1/memory")).status_code == 401


async def test_回填有界幂等并发与删除会话级联(
    client, auth_headers, db, memory_enabled, monkeypatch
):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    first = await chat_history_service.add_message(owner, "delete-me", "user", "不吃辣" * 1000)
    await chat_history_service.add_message(owner, "keep", "assistant", "以前的回答")
    await memory_service.apply_cards(owner, first["id"], [patch("不吃辣")])
    monkeypatch.setattr(settings, "MEMORY_BACKFILL_BATCH", 1)
    results = await asyncio.gather(
        memory_service.index_history(owner), memory_service.index_history(owner)
    )
    assert sum(r["indexed_chunks"] for r in results) == 4
    assert all(r["has_more"] for r in results)
    result = (await client.post("/api/v1/memory/backfill", headers=auth_headers)).json()["data"]
    assert result["indexed_chunks"] == 1 and not result["has_more"]
    assert (await memory_service.index_history(owner))["indexed_chunks"] == 0
    await chat_history_service.add_message(owner, "keep", "assistant", " \n\t ")
    # 空白消息不应永远占据第一批，或让 has_more 永远为 true。
    assert not (await memory_service.index_history(owner))["has_more"]
    response = await client.delete("/api/v1/chat/conversations/delete-me", headers=auth_headers)
    assert response.json()["code"] == 0
    assert await memory_service.list_cards(owner) == []
    assert await db.scalar(select(func.count()).select_from(MemoryEpisode)) == 1


async def test_清空期间在途写入和回填不能复活旧记忆(db, auth_headers, memory_enabled, monkeypatch):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    source = await chat_history_service.add_message(owner, "clear", "user", "不吃辣")
    started, resume = asyncio.Event(), asyncio.Event()

    async def blocked_embeddings(texts):
        started.set()
        await resume.wait()
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(embedding_client, "embed_texts", blocked_embeddings)
    job = asyncio.create_task(memory_service.index_history(owner))
    await started.wait()
    await memory_service.clear_memory(owner)
    resume.set()
    assert (await job)["indexed_chunks"] == 0
    assert await memory_service.apply_cards(owner, source["id"], [patch("不吃辣")]) == 0
    assert (await memory_service.index_history(owner))["indexed_chunks"] == 0
    assert await memory_service.search_history(owner, "不吃辣") == []
    # 原始聊天保留，新对话仍然能形成新记忆。
    assert await chat_history_service.get_recent_history(owner, "clear")
    fresh = await chat_history_service.add_message(owner, "fresh", "user", "喜欢微辣")
    assert await memory_service.apply_cards(owner, fresh["id"], [patch("喜欢微辣")]) == 1


@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_模型故障保留回答历史且匿名不写记忆(
    client, auth_headers, db, memory_model, monkeypatch, path
):
    async def broken(*args, **kwargs):
        raise RuntimeError("模型暂不可用")

    monkeypatch.setattr(memory_service, "ainvoke_json", broken)
    monkeypatch.setattr(embedding_client, "embed_texts", broken)
    answer = await ask(client, path, "我不吃辣", "failure", auth_headers)
    assert answer
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2
    assert await db.scalar(select(func.count()).select_from(MemoryCard)) == 0
    assert await db.scalar(select(func.count()).select_from(MemoryEpisode)) == 0
    await ask(client, path, "我不吃辣", "anon", None)
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 2


async def test_卡片和历史字符预算及最近消息去重(db, auth_headers, memory_enabled, monkeypatch):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    message = await chat_history_service.add_message(owner, "recent", "user", "不吃辣")
    await memory_service.apply_cards(owner, message["id"], [patch("不吃辣")])
    await memory_service.index_history(owner)
    context = await memory_service.prepare_context(owner, "recent", "之前说过什么")
    data = json.loads(context[context.index("{") :])
    assert data["cards"] and data["history"] == []
    monkeypatch.setattr(settings, "MEMORY_CARD_LIMIT", 2)
    await chat_history_service.add_message(owner, "recent", "assistant", "模型认为用户有过敏")
    more = await chat_history_service.add_message(owner, "recent", "user", "喜欢散步喜欢安静")
    await memory_service.apply_cards(
        owner, more["id"], [patch("喜欢散步", key="walk"), patch("喜欢安静", key="quiet")]
    )
    assert len(await memory_service.list_cards(owner)) == 2
    bounded = memory_service._bounded([{"value": "a" * 600}, {"value": "b"}], 30)
    assert bounded == [{"value": "b"}]


async def test_历史检索失败仍注入卡片(db, auth_headers, memory_enabled, monkeypatch):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    source = await chat_history_service.add_message(owner, "source", "user", "不吃辣")
    await memory_service.apply_cards(owner, source["id"], [patch("不吃辣")])

    async def broken(*args, **kwargs):
        raise RuntimeError("检索模型故障")

    monkeypatch.setattr(memory_service, "search_history", broken)
    context = await memory_service.prepare_context(owner, "new", "之前的饮食限制是什么")
    assert "不吃辣" in context


async def test_空记忆也明确告知模型支持记忆(db, auth_headers, memory_enabled):
    owner = await db.scalar(select(User.id).where(User.username == "tester"))
    context = await memory_service.prepare_context(owner, "empty", "之前的暗号是什么")
    assert "系统支持跨会话长期记忆" in context
    assert json.loads(context[context.index("{") :]) == {"cards": [], "history": []}
    assert await memory_service.prepare_context(None, "empty", "之前的暗号是什么") == ""
