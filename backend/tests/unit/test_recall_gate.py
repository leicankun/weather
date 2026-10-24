"""Recall gate semantics, bounded history IO and real graph/HTTP integration (no DB)."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.core.config import settings
from app.core.deps import get_optional_user
from app.main import app
from app.routers import chat as chat_router
from app.services import agent, intent_router, memory_service, multi_agent, recall_gate
from tests.unit.test_agent_intent import AnswerModel


@pytest.mark.parametrize(
    ("question", "needed", "only"),
    [
        ("那家餐厅叫什么？", True, True),
        ("我常住在哪里？", True, True),
        ("根据我平时的口味推荐晚餐", True, False),
        ("历史上广州发生过哪些台风？", False, False),
        ("以前的广州叫什么名字？", False, False),
        ("请记得提醒游客带伞", False, False),
        ("广州明天天气怎么样？", False, False),
    ],
)
async def test_vectors_replace_recall_word_triggers(question, needed, only):
    decision = await recall_gate.recall_gate.decide(question)
    assert decision.need_recall is needed and decision.history_only is only


async def test_followups_inherit_only_current_conversation_user_topic():
    history = [{"role": "user", "content": "我上次说过的餐厅叫什么？"}]
    decision = await recall_gate.recall_gate.decide("那后来呢？", history)
    assert decision.need_recall and decision.history_only and decision.status == "inherited"
    assert history[0]["content"] in decision.query and "那后来呢" in decision.query
    assert not (await recall_gate.recall_gate.decide("那后来呢？")).need_recall
    assert not (
        await recall_gate.recall_gate.decide(
            "那后天呢？", [{"role": "user", "content": "广州明天天气怎么样？"}]
        )
    ).need_recall
    history.append({"role": "user", "content": "谢谢你"})
    assert not (await recall_gate.recall_gate.decide("那后来呢？", history)).need_recall
    assert not (
        await recall_gate.recall_gate.decide(
            "那后来呢？", [{"role": "assistant", "content": "我上次说过的餐厅叫什么？"}]
        )
    ).need_recall


async def test_context_window_summary_fallback_and_threshold(monkeypatch):
    history = [
        {"role": "user", "content": "我上次说过的餐厅叫什么？"},
        {"role": "user", "content": "那后来呢？"},
    ]
    monkeypatch.setattr(settings, "MEMORY_RECALL_CONTEXT_TURNS", 1)
    assert not (await recall_gate.recall_gate.decide("还有细节吗？", history)).need_recall
    assert (
        await recall_gate.recall_gate.decide(
            "那后来呢？", [{"role": "summary", "content": "我上次说过的餐厅叫什么？"}]
        )
    ).need_recall
    monkeypatch.setattr(settings, "MEMORY_RECALL_THRESHOLD", 1.01)
    decision = await recall_gate.recall_gate.decide("那家餐厅叫什么？")
    assert not decision.need_recall and decision.status == "uncertain"


@pytest.mark.parametrize("failure", ["key", "error", "dimension", "zero", "timeout"])
async def test_strict_embedding_failure_is_explicit_without_fallback(
    monkeypatch, recall_embeddings, failure
):
    provider, _ = recall_embeddings
    if failure == "key":
        monkeypatch.setattr(provider, "available", False)
    else:

        async def broken(self, texts, **kwargs):
            if failure == "error":
                raise httpx.ConnectError("fixture")
            if failure == "timeout":
                await asyncio.sleep(1)
            return [[0.0] * (self.dim - 1 if failure == "dimension" else self.dim) for _ in texts]

        monkeypatch.setattr(provider, "embed_texts", broken)
    monkeypatch.setattr(settings, "MEMORY_RECALL_TIMEOUT", 0.02)
    decision = await recall_gate.recall_gate.decide("那家餐厅叫什么？")
    assert not decision.need_recall and decision.status == "unavailable"


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_ENABLED", True)
    cards = AsyncMock(return_value=[{"key": "diet", "data": {"value": "不吃辣"}}])
    index = AsyncMock(return_value={})
    search = AsyncMock(
        return_value=[{"source": "chat:old:7", "content": "用户17提过的餐厅是云舟茶馆"}]
    )
    statements = []

    class DB:
        async def scalars(self, statement):
            statements.append(statement.compile().params)
            return SimpleNamespace(all=lambda: [77])

    @asynccontextmanager
    async def session():
        yield DB()

    monkeypatch.setattr(memory_service, "list_cards", cards)
    monkeypatch.setattr(memory_service, "index_history", index)
    monkeypatch.setattr(memory_service, "search_history", search)
    monkeypatch.setattr(memory_service, "AsyncSessionLocal", session)
    return cards, index, search, statements


def payload(context):
    return json.loads(context[len(memory_service.MEMORY_RULES) :])


async def test_prepare_context_reads_cards_always_and_searches_only_on_vector_match(repository):
    cards, index, search, statements = repository
    normal = payload(await memory_service.prepare_context(17, "new", "历史上广州发生过哪些台风？"))
    assert normal["cards"] and normal["history"] == [] and not search.called and not index.called
    recalled = payload(await memory_service.prepare_context(17, "new", "那家餐厅叫什么？"))
    assert recalled["history"] and recalled["recall"]["retrieval_status"] == "found"
    search.assert_awaited_once_with(17, "那家餐厅叫什么？", settings.MEMORY_HISTORY_TOP_K, [77])
    assert 17 in statements[0].values() and "new" in statements[0].values()
    assert cards.await_count == 2 and index.await_count == 1
    assert "query" not in recalled["recall"]


async def test_user_scope_and_disabled_anonymous_memory(repository, monkeypatch):
    cards, _index, search, statements = repository
    for uid in (17, 18):
        await memory_service.prepare_context(uid, "same", "那家餐厅叫什么？")
    assert [call.args[0] for call in search.await_args_list] == [17, 18]
    assert 17 in statements[0].values() and 18 in statements[1].values()
    assert await memory_service.prepare_context(None, "same", "那家餐厅叫什么？") == ""
    monkeypatch.setattr(settings, "MEMORY_ENABLED", False)
    assert await memory_service.prepare_context(17, "same", "那家餐厅叫什么？") == ""
    assert cards.await_count == 2


async def test_service_failures_preserve_cards_and_distinguish_empty_from_failed(
    repository, monkeypatch, recall_embeddings
):
    _, _, search, _ = repository
    search.return_value = []
    empty = payload(await memory_service.prepare_context(17, "new", "那家餐厅叫什么？"))
    assert empty["recall"]["retrieval_status"] == "empty"
    search.side_effect = RuntimeError("fixture outage")
    failed = payload(await memory_service.prepare_context(17, "new", "那家餐厅叫什么？"))
    assert failed["cards"] and failed["recall"]["retrieval_status"] == "failed"
    provider, _ = recall_embeddings
    monkeypatch.setattr(provider, "available", False)
    failed = payload(await memory_service.prepare_context(17, "new", "那家餐厅叫什么？"))
    assert failed["cards"] and failed["recall"]["status"] == "unavailable"
    assert search.await_count == 2


async def test_pure_recall_bypasses_business_classifier_but_not_management(
    repository, semantic_embeddings, monkeypatch
):
    context = await memory_service.prepare_context(17, "new", "那家餐厅叫什么？")
    provider, _ = semantic_embeddings
    monkeypatch.setattr(provider, "available", False)
    decision = await intent_router.identify_intent("那家餐厅叫什么？", memory_context=context)
    assert decision.status == "general" and not decision.domains
    assert (
        await intent_router.identify_intent("清空我的记忆", memory_context=context)
    ).domains == ["management"]
    assert not memory_service.history_only_question('{"recall":{"history_only":true}}')
    assert not memory_service.history_only_question(memory_service.MEMORY_RULES + "invalid")


async def test_checkpoint_resume_reuses_recalled_context_and_new_turn_clears_it(
    repository, monkeypatch
):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Overwrite

    context = await memory_service.prepare_context(17, "new", "那家餐厅叫什么？")
    model = MemoryAnswerModel(messages=iter(()))
    original = agent._agent_node
    fail = True

    async def once(state):
        nonlocal fail
        if fail:
            fail = False
            raise RuntimeError("fixture generation interruption")
        return await original(state)

    monkeypatch.setattr(agent, "_agent_node", once)
    monkeypatch.setattr(agent, "_get_all_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    graph = await agent._build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "recall-resume"}}
    with pytest.raises(RuntimeError, match="interruption"):
        await graph.ainvoke(
            agent._build_initial_state("那家餐厅叫什么？", memory_context=context), config=config
        )
    saved = await graph.aget_state(config)
    assert saved.values["memory_context"] == context and saved.next == ("agent",)
    result = await graph.ainvoke(None, config=config)
    assert result["answer"] == "那家餐厅是云舟茶馆" and repository[2].await_count == 1
    initial = agent._build_initial_state("你好呀")
    initial["messages"] = Overwrite(initial["messages"])
    result = await graph.ainvoke(initial, config=config)
    assert result["memory_context"] == "" and result["answer"] == "已收到问题"


class MemoryAnswerModel(AnswerModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(messages)
        system = messages[0].content
        answer = "那家餐厅是云舟茶馆" if "用户17提过的餐厅是云舟茶馆" in system else "已收到问题"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._generate(messages).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=response.content))


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("stream", [False, True])
async def test_authenticated_http_and_sse_recall_without_old_keywords(
    monkeypatch, repository, mode, stream
):
    _, _, search, _ = repository
    model = MemoryAnswerModel(messages=iter(()))
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(multi_agent, "load_domain_tools", AsyncMock(return_value={}))
    monkeypatch.setattr(agent, "_agent_instance", None)
    monkeypatch.setattr(multi_agent, "_graph", None)
    monkeypatch.setattr(
        chat_router, "_with_preferences", AsyncMock(side_effect=lambda prompt, uid: prompt)
    )
    monkeypatch.setattr(chat_router, "_save_turn", AsyncMock())
    monkeypatch.setattr(chat_router.chat_summary_service, "get_context", AsyncMock(return_value=[]))
    monkeypatch.setitem(app.dependency_overrides, get_optional_user, lambda: SimpleNamespace(id=17))
    spy = AsyncMock(wraps=memory_service.recall_gate.decide)
    monkeypatch.setattr(memory_service.recall_gate, "decide", spy)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat" + ("/stream" if stream else ""), json={"message": "那家餐厅叫什么？"}
        )
    assert response.status_code == 200 and spy.await_count == search.await_count == 1
    if stream:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
        answer = "".join(item.get("content", "") for item in events if item["type"] == "token")
    else:
        answer = response.json()["data"]["answer"]
    assert answer == "那家餐厅是云舟茶馆" and model.inputs
