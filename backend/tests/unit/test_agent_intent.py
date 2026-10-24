"""Semantic routing through real single/multi graphs and chat HTTP/SSE."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from app.core.config import settings
from app.main import app
from app.services import agent, intent_router, multi_agent
from app.services.intent_examples import INTENT_EXAMPLES
from app.services.intent_router import UNAVAILABLE_ANSWER, UNCERTAIN_ANSWER, SemanticIntentRouter


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("出门要不要带伞？", ["weather"]),
        ("薄外套够不够？", ["weather", "outfit"]),
        ("明天去广州塔穿什么、怎么走", ["weather", "outfit", "route"]),
        ("不用查天气，只告诉我怎么去", ["route"]),
        ("你好呀", []),
    ],
)
async def test_semantic_examples_select_domains(question, expected):
    decision = await intent_router.identify_intent(question)
    assert decision.domains == expected
    assert decision.status in {"matched", "general"}


async def test_followup_inherits_multiple_domains_and_not_assistant_suggestions():
    history = [
        {"role": "user", "content": "明天去广州塔穿什么、怎么走"},
        {"role": "assistant", "content": "还可以查美食和酒店"},
    ]
    original = copy.deepcopy(history)
    decision = await intent_router.identify_intent("那后天呢", history)
    assert decision.domains == ["weather", "outfit", "route"] and decision.inherited
    assert history == original


async def test_explicit_topic_overrides_history_and_chitchat_breaks_inheritance():
    history = [{"role": "user", "content": "广州天气"}]
    assert (await intent_router.identify_intent("广州有什么好吃的？", history)).domains == [
        "knowledge"
    ]
    history.append({"role": "user", "content": "谢谢你"})
    assert (await intent_router.identify_intent("那后天呢", history)).status == "uncertain"
    assert (await intent_router.identify_intent("那后天呢", [])).status == "uncertain"


async def test_followup_chain_is_bounded_and_conversations_are_isolated(monkeypatch):
    history = [{"role": "user", "content": "广州天气"}, {"role": "user", "content": "那明天呢"}]
    assert (await intent_router.identify_intent("那后天呢", history)).domains == ["weather"]
    monkeypatch.setattr(settings, "INTENT_CONTEXT_TURNS", 1)
    assert (await intent_router.identify_intent("那后天呢", history)).status == "uncertain"
    assert (
        await intent_router.identify_intent("那后天呢", [{"role": "user", "content": "怎么去"}])
    ).domains == ["route"]
    assert (await intent_router.identify_intent("那后天呢")).domains == []


async def test_low_scores_request_clarification_without_keyword_or_llm_fallback(
    semantic_embeddings,
):
    provider, meanings = semantic_embeddings
    # A vector orthogonal to every route; the input deliberately includes the old keyword.
    meanings["天气"] = ()
    original = provider.embed_texts

    async def embed(self, texts, **kwargs):
        values = await original(self, texts, **kwargs)
        for index, text in enumerate(texts):
            if text == "天气":
                values[index][-1] = 1
        return values

    provider.embed_texts = embed
    decision = await intent_router.identify_intent("天气")
    assert decision.status == "uncertain" and not decision.domains
    assert decision.stop_answer == UNCERTAIN_ANSWER


@pytest.mark.parametrize(
    "failure", ["missing", "error", "count", "zero", "nan", "dimension", "timeout"]
)
async def test_provider_failures_never_become_general_or_hash_vectors(
    monkeypatch, semantic_embeddings, failure
):
    provider, _ = semantic_embeddings
    if failure == "missing":
        monkeypatch.setattr(provider, "available", False)
    else:

        async def broken(self, texts, **kwargs):
            if failure == "error":
                raise RuntimeError("private provider details")
            if failure == "timeout":
                await asyncio.sleep(1)
            size = self.dim - 1 if failure == "dimension" else self.dim
            value = float("nan") if failure == "nan" else 0.0
            return [] if failure == "count" else [[value] * size for _ in texts]

        monkeypatch.setattr(provider, "embed_texts", broken)
    monkeypatch.setattr(settings, "INTENT_ROUTING_TIMEOUT", 0.02)
    decision = await intent_router.identify_intent("广州天气")
    assert decision.status == "unavailable" and decision.stop_answer == UNAVAILABLE_ANSWER
    assert "private" not in decision.stop_answer


async def test_concurrent_warmup_and_model_change_rebuild_examples(
    semantic_embeddings, monkeypatch
):
    provider, _ = semantic_embeddings
    router = SemanticIntentRouter()
    await asyncio.gather(*(router.route("广州天气") for _ in range(5)))
    first = INTENT_EXAMPLES["weather"][0]
    assert sum(first in batch for batch in provider.calls) == 1
    monkeypatch.setattr(provider, "model", "changed-model")
    await router.route("广州天气")
    assert sum(first in batch for batch in provider.calls) == 2


async def test_management_remains_exclusive_and_requires_no_embedding(semantic_embeddings):
    provider, _ = semantic_embeddings
    decision = await intent_router.identify_intent("清空我的记忆，顺便查询天气")
    assert decision.domains == ["management"] and not provider.calls
    assert multi_agent._route_from_supervisor({"domains": decision.domains}) == ["prepare_action"]


async def test_preferences_and_attachment_body_do_not_create_extra_domains():
    for suffix in ("【用户偏好】（以下内容回答时请遵循）\n怎么穿", "【附件：资料】\n怎么走"):
        decision = await intent_router.identify_intent("广州天气\n\n" + suffix)
        assert decision.domains == ["weather"]


async def test_resume_uses_saved_routing_even_when_embedding_provider_is_down(
    monkeypatch, semantic_embeddings
):
    provider, _ = semantic_embeddings
    monkeypatch.setattr(provider, "available", False)
    saved = {"domains": ["weather", "outfit"], "routing": {"status": "matched"}, "routing_stop": ""}
    context = SimpleNamespace(
        resume=True,
        config={},
        saver=SimpleNamespace(
            aget_tuple=AsyncMock(return_value=SimpleNamespace(checkpoint={"channel_values": saved}))
        ),
    )
    monkeypatch.setattr(multi_agent.checkpoint_service, "current", lambda: context)
    assert await multi_agent.plan_routing("那后天呢") == saved
    assert not provider.calls


class AnswerModel(GenericFakeChatModel):
    inputs: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(messages)
        text = (
            '{"passed":true,"issues":[],"revised_answer":""}'
            if "独立的回答验证员" in messages[0].content
            else "已按本次问题回答"
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._generate(messages).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(content=response.content))


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("stop", ["none", "unavailable", "uncertain"])
async def test_http_and_sse_use_same_router_once_and_stop_before_generation(
    monkeypatch, semantic_embeddings, mode, stream, stop
):
    provider, _ = semantic_embeddings
    model = AnswerModel(messages=iter(()))
    monkeypatch.setattr(settings, "AGENT_MODE", mode)
    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(multi_agent, "load_domain_tools", AsyncMock(return_value={}))
    monkeypatch.setattr(agent, "_agent_instance", None)
    monkeypatch.setattr(multi_agent, "_graph", None)
    if stop == "unavailable":
        monkeypatch.setattr(provider, "available", False)
    spy = AsyncMock(wraps=intent_router.identify_intent)
    monkeypatch.setattr(intent_router, "identify_intent", spy)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat" + ("/stream" if stream else ""),
            json={"message": "那后天呢" if stop == "uncertain" else "广州天气"},
        )
    assert response.status_code == 200 and spy.await_count == 1
    if stream:
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
        answer = "".join(item.get("content", "") for item in events if item["type"] == "token")
        assert next(item for item in events if item["type"] == "intent")["intent"] == (
            "weather" if stop == "none" else "other"
        )
    else:
        answer = response.json()["data"]["answer"]
    assert (
        answer
        == {
            "none": "已按本次问题回答",
            "unavailable": UNAVAILABLE_ANSWER,
            "uncertain": UNCERTAIN_ANSWER,
        }[stop]
    )
    assert bool(model.inputs) == (stop == "none")


async def test_single_graph_saves_multilabel_decision_for_following_nodes(monkeypatch):
    monkeypatch.setattr(agent, "_get_all_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent, "get_llm", lambda: AnswerModel(messages=iter(())))
    graph = await agent._build_graph()
    result = await graph.ainvoke(agent._build_initial_state("明天去广州塔穿什么、怎么走"))
    assert result["domains"] == ["weather", "outfit", "route"]
    assert result["intent"] == "travel" and result["routing"]["status"] == "matched"


async def test_cosine_threshold_and_multidomain_margin(monkeypatch, semantic_embeddings):
    provider, meanings = semantic_embeddings
    meanings["混合请求"] = ("weather", "route")
    original = provider.embed_texts

    async def embed(self, texts, **kwargs):
        vectors = await original(self, texts, **kwargs)
        for index, utterance in enumerate(texts):
            if utterance == "混合请求":
                vectors[index] = [0.8, 0.0, 0.6] + [0.0] * (self.dim - 3)
        return vectors

    monkeypatch.setattr(provider, "embed_texts", embed)
    decision = await intent_router.identify_intent("混合请求")
    assert decision.domains == ["weather"]
    assert decision.scores["weather"] == 0.8 and decision.scores["route"] == 0.6
    monkeypatch.setattr(settings, "INTENT_MULTI_MARGIN", 0.21)
    assert (await intent_router.identify_intent("混合请求")).domains == ["weather", "route"]
    monkeypatch.setattr(settings, "INTENT_SIMILARITY_THRESHOLD", 0.9)
    assert (await intent_router.identify_intent("混合请求")).status == "uncertain"


async def test_two_nearest_examples_reduce_one_mislabelled_outlier(
    monkeypatch, semantic_embeddings
):
    provider, _ = semantic_embeddings
    original = provider.embed_texts

    async def embed(self, texts, **kwargs):
        vectors = await original(self, texts, **kwargs)
        for index, utterance in enumerate(texts):
            if utterance == INTENT_EXAMPLES["weather"][0]:
                vectors[index] = [0.0, 0.0, 1.0] + [0.0] * (self.dim - 3)
        return vectors

    monkeypatch.setattr(provider, "embed_texts", embed)
    decision = await intent_router.identify_intent("怎么去")
    assert decision.domains == ["route"] and decision.scores["weather"] == 0.5


async def test_failed_warmup_is_not_cached_and_next_request_can_recover(
    monkeypatch, semantic_embeddings
):
    provider, _ = semantic_embeddings
    original = provider.embed_texts
    calls = 0

    async def fail_once(self, texts, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ConnectError("fixture outage")
        return await original(self, texts, **kwargs)

    monkeypatch.setattr(provider, "embed_texts", fail_once)
    assert (await intent_router.identify_intent("广州天气")).status == "unavailable"
    assert (await intent_router.identify_intent("广州天气")).domains == ["weather"]
    assert sum(INTENT_EXAMPLES["weather"][0] in batch for batch in provider.calls) == 2


async def test_checkpoint_stores_routing_and_new_turn_resets_it(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Overwrite

    monkeypatch.setattr(agent, "_get_all_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent, "get_llm", lambda: AnswerModel(messages=iter(())))
    graph = await agent._build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "semantic-routing"}}
    await graph.ainvoke(agent._build_initial_state("明天去广州塔穿什么、怎么走"), config=config)
    saved = await graph.aget_state(config)
    assert saved.values["routing"]["domains"] == ["weather", "outfit", "route"]
    initial = agent._build_initial_state("广州有什么好吃的")
    initial["messages"] = Overwrite(initial["messages"])
    await graph.ainvoke(initial, config=config)
    saved = await graph.aget_state(config)
    assert saved.values["domains"] == ["knowledge"] and saved.values["routing_stop"] == ""
