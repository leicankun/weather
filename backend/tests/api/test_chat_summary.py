"""真实 PostgreSQL：五轮检查、增量摘要、并发与普通/SSE 接入。"""

import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy import func, select

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.chat_summary import ChatSummary
from app.models.user import User
from app.schemas.chat_summary import SummaryContent
from app.services import (
    agent,
    chat_history_service,
    chat_summary_service,
    memory_service,
    multi_agent,
)


@pytest.fixture
def summaries(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_TRIGGER_CHARS", 6000)
    calls = []

    async def summarize(prompt, schema, **kwargs):
        calls.append(json.loads(prompt))
        return SummaryContent(
            summary=f"摘要第{len(calls)}版：用户准备周六去广州，少步行；助手建议尚未确认。"
        )

    monkeypatch.setattr(chat_summary_service, "ainvoke_json", summarize)
    return calls


async def owner(db):
    user = User(username="summary_owner", password_hash="unused")
    db.add(user)
    await db.commit()
    return user.id


async def seed(user_id, conversation_id, start=1, stop=10, *, check=False, long=True):
    ids = []
    for index in range(start, stop + 1):
        text = f"第{index}轮用户原话" + ("出行条件" * 100 if long else "")
        user_message, answer = await chat_history_service.add_turn(
            user_id,
            conversation_id,
            text,
            f"第{index}轮助手建议" + ("路线建议" * 100 if long else ""),
        )
        ids.extend([user_message, answer])
        if check:
            await chat_summary_service.check_and_compress(user_id, conversation_id, answer)
    return ids


async def test_只在五轮检查点超预算时压缩并保留过渡消息(db, summaries):
    uid = await owner(db)
    await seed(uid, "long", stop=9, check=True)
    assert summaries == []  # 第五轮没有可压缩的旧消息；第六至九轮不调用模型。
    context = await chat_summary_service.get_context(uid, "long")
    assert len(context) == 18 and "第1轮" in context[0]["content"]
    await seed(uid, "long", start=10, stop=10, check=True)
    assert len(summaries) == 1
    assert len(summaries[0]["messages"]) == 10
    assert "第5轮" in summaries[0]["messages"][-1]["content"]
    assert all("第6轮" not in item["content"] for item in summaries[0]["messages"])
    context = await chat_summary_service.get_context(uid, "long")
    assert len(context) == 11 and context[0]["role"] == "summary"
    assert "第6轮" in context[1]["content"]
    await seed(uid, "long", start=11, stop=14, check=True)
    context = await chat_summary_service.get_context(uid, "long")
    assert len(context) == 19 and "第6轮" in context[1]["content"]
    assert len(summaries) == 1
    await seed(uid, "long", start=15, stop=15, check=True)
    assert len(summaries) == 2
    assert "摘要第1版" in summaries[1]["previous_summary"]
    assert "第6轮" in summaries[1]["messages"][0]["content"]
    assert "第10轮" in summaries[1]["messages"][-1]["content"]
    context = await chat_summary_service.get_context(uid, "long")
    assert len(context) == 11 and "第11轮" in context[1]["content"]
    assert await db.scalar(select(func.count()).select_from(ChatMessage)) == 30


async def test_短对话只检查不调用模型且隔离用户会话匿名和开关(db, summaries, monkeypatch):
    uid = await owner(db)
    await seed(uid, "short", stop=15, check=True, long=False)
    assert summaries == []
    state = await db.get(ChatSummary, (uid, "short"))
    assert state.last_checked_turn == 15 and state.summary == ""
    assert len(await chat_summary_service.get_context(uid, "short")) == 30
    assert await chat_summary_service.get_context(None, "short") == []
    assert await chat_summary_service.get_context(uid, "other") == []
    other = User(username="summary_other", password_hash="unused")
    db.add(other)
    await db.commit()
    assert await chat_summary_service.get_context(other.id, "short") == []
    assert not await chat_summary_service.check_and_compress(other.id, "short", 2)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", False)
    assert len(await chat_summary_service.get_context(uid, "short")) == 10
    assert not await chat_summary_service.check_and_compress(uid, "short", 30)


async def test_同一检查点并发只调用一次摘要模型(db, summaries):
    uid = await owner(db)
    ids = await seed(uid, "same")
    results = await asyncio.gather(
        chat_summary_service.check_and_compress(uid, "same", ids[-1]),
        chat_summary_service.check_and_compress(uid, "same", ids[-1]),
    )
    assert sorted(results) == [False, True] and len(summaries) == 1


async def test_同会话并发保存仍保持完整轮次顺序(db):
    uid = await owner(db)
    await asyncio.gather(
        *(chat_history_service.add_turn(uid, "pairs", f"用户{n}", f"助手{n}") for n in range(8))
    )
    rows = await chat_history_service.get_conversation_messages(uid, "pairs", db=db)
    assert len(rows) == 16
    for index in range(0, 16, 2):
        assert [rows[index]["role"], rows[index + 1]["role"]] == ["user", "assistant"]
        assert rows[index]["content"][2:] == rows[index + 1]["content"][2:]


async def test_历史RAG排除全部未覆盖原文但允许回忆已摘要消息(db, summaries, monkeypatch):
    uid = await owner(db)
    ids = await seed(uid, "dedup", check=True)
    ids.extend(await seed(uid, "dedup", start=11, stop=14, check=True))
    monkeypatch.setattr(settings, "MEMORY_ENABLED", True)
    excluded = []

    async def index(*args, **kwargs):
        return {}

    async def search(user_id, query, top_k, exclude_ids):
        excluded.extend(exclude_ids)
        return []

    monkeypatch.setattr(memory_service, "index_history", index)
    monkeypatch.setattr(memory_service, "search_history", search)
    await memory_service.prepare_context(uid, "dedup", "之前讨论了什么")
    assert set(excluded) == set(ids[10:]) and len(excluded) == 18


async def test_旧检查的慢结果不能覆盖新摘要(db, summaries, monkeypatch):
    uid = await owner(db)
    ids = await seed(uid, "race")
    started, release = asyncio.Event(), asyncio.Event()

    async def summarize(prompt, schema, **kwargs):
        payload = json.loads(prompt)
        if "第1轮" in payload["messages"][0]["content"] and len(payload["messages"]) == 10:
            started.set()
            await release.wait()
            return SummaryContent(summary="旧摘要")
        return SummaryContent(summary="新摘要：已处理前十轮")

    monkeypatch.setattr(chat_summary_service, "ainvoke_json", summarize)
    older = asyncio.create_task(chat_summary_service.check_and_compress(uid, "race", ids[-1]))
    await asyncio.wait_for(started.wait(), 5)
    new_ids = await seed(uid, "race", start=11, stop=15)
    assert await chat_summary_service.check_and_compress(uid, "race", new_ids[-1])
    release.set()
    assert not await older
    state = await db.get(ChatSummary, (uid, "race"))
    assert state.summary == "新摘要：已处理前十轮" and state.covered_message_id == ids[-1]


async def test_删除会话不能被在途摘要复活且长期清空不影响短期摘要(db, summaries, monkeypatch):
    uid = await owner(db)
    ids = await seed(uid, "delete")
    started, release = asyncio.Event(), asyncio.Event()

    async def summarize(*args, **kwargs):
        started.set()
        await release.wait()
        return SummaryContent(summary="不应复活的旧摘要")

    monkeypatch.setattr(chat_summary_service, "ainvoke_json", summarize)
    job = asyncio.create_task(chat_summary_service.check_and_compress(uid, "delete", ids[-1]))
    await asyncio.wait_for(started.wait(), 5)
    assert await chat_history_service.delete_conversation(uid, "delete") == 20
    # 复用会话 ID 创建新记录，旧结果的来源消息也必须仍存在。
    new_ids = await seed(uid, "delete", long=False)
    await chat_summary_service.check_and_compress(uid, "delete", new_ids[-1])
    release.set()
    assert not await job
    state = await db.get(ChatSummary, (uid, "delete"))
    assert state.summary == "" and state.covered_message_id is None
    # 无论长期记忆开关，清空都不删除短期上下文与检查状态。
    await memory_service.clear_memory(uid)
    assert len(await chat_summary_service.get_context(uid, "delete")) == 20
    await chat_history_service.delete_conversation(uid, "delete")
    async with chat_summary_service.AsyncSessionLocal() as fresh:
        assert await fresh.get(ChatSummary, (uid, "delete")) is None


@pytest.mark.parametrize("failure", ["exception", "timeout", "oversize", "empty"])
async def test_模型故障不丢原始消息且下一检查点重试(db, summaries, monkeypatch, failure):
    uid = await owner(db)
    ids = await seed(uid, "failure")

    async def broken(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("model unavailable")
        if failure == "timeout":
            await asyncio.sleep(1)
        if failure == "empty":
            return {"summary": "  "}
        return SummaryContent(summary="过长" * 700)

    original = chat_summary_service.ainvoke_json
    monkeypatch.setattr(chat_summary_service, "ainvoke_json", broken)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_TIMEOUT", 0.05 if failure == "timeout" else 20)
    assert not await chat_summary_service.check_and_compress(uid, "failure", ids[-1])
    assert len(await chat_summary_service.get_context(uid, "failure")) == 20
    monkeypatch.setattr(chat_summary_service, "ainvoke_json", original)
    monkeypatch.setattr(settings, "CHAT_SUMMARY_TIMEOUT", 20)
    ids = await seed(uid, "failure", start=11, stop=15)
    assert await chat_summary_service.check_and_compress(uid, "failure", ids[-1])


class ContextModel(GenericFakeChatModel):
    calls: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "独立的回答验证员" in messages[0].content:
            answer = '{"passed":true,"issues":[],"revised_answer":""}'
        elif "意图识别模块" in messages[0].content:
            answer = "other"
        else:
            self.calls.append(list(messages))
            answer = "已接收上下文"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


@pytest.fixture
def context_model(monkeypatch):
    model = ContextModel(messages=iter(()))
    agent._agent_instance = None
    multi_agent.reset_cache()
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)

    async def tools():
        return []

    async def domains():
        return {}

    async def route(*args, **kwargs):
        return multi_agent.DomainChoice(domains=[])

    monkeypatch.setattr(agent, "_get_all_tools", tools)
    monkeypatch.setattr(multi_agent, "load_domain_tools", domains)
    monkeypatch.setattr(multi_agent, "ainvoke_json", route)
    yield model
    agent._agent_instance = None
    multi_agent.reset_cache()


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_普通和SSE统一写摘要且下一轮传给各节点(
    client, auth_headers, db, summaries, context_model, monkeypatch, mode, path
):
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    uid = await db.scalar(select(User.id).where(User.username == "tester"))
    await seed(uid, "api", stop=9, check=True)
    response = await client.post(
        path, headers=auth_headers, json={"message": "继续", "conversation_id": "api"}
    )
    assert response.status_code == 200
    if path.endswith("stream"):
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
    assert len(summaries) == 1
    assert len(await chat_summary_service.get_context(uid, "api")) == 11
    context_model.calls.clear()
    response = await client.post(
        path, headers=auth_headers, json={"message": "天气和穿什么", "conversation_id": "api"}
    )
    assert response.status_code == 200
    assert context_model.calls
    for messages in context_model.calls:
        assert any("摘要第1版" in str(message.content) for message in messages)
        assert any("第6轮用户原话" in str(message.content) for message in messages)
        assert all("第1轮用户原话" not in str(message.content) for message in messages)
    raw = await client.get("/api/v1/chat/conversations/api", headers=auth_headers)
    assert raw.json()["data"]["total"] == 22
    # 匿名请求不接收已有摘要，也不会触发写入。
    context_model.calls.clear()
    await client.post(path, json={"message": "继续", "conversation_id": "api"})
    assert all("摘要第" not in str(messages) for messages in context_model.calls)
    deleted = await client.delete("/api/v1/chat/conversations/api", headers=auth_headers)
    assert deleted.status_code == 200
    async with chat_summary_service.AsyncSessionLocal() as fresh:
        assert await fresh.get(ChatSummary, (uid, "api")) is None
