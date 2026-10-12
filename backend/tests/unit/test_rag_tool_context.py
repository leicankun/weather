"""Exercise actual RAG tools through single/multi graphs with observable model inputs."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from app.scripts.compare_agents import _run_multi, _run_single
from app.services import agent, local_tools, multi_agent, rag_service, user_knowledge_service
from app.services.answer_verification import evidence_record
from app.services.knowledge_tool_context import (
    MAX_KNOWLEDGE_TOOL_CHARS,
    format_knowledge_context,
)
from app.services.user_context import get_current_user_id
from mcp_server import tools as mcp_business

FACTS = ["16:47", "西门C3", "紫鹭928"]
CONTENT = (
    "云舟活动资料。"
    + "场馆设有阅读区和展览区，参加者遵守秩序并保管随身物品。" * 8
    + "最终安排：集合时间16:47，入口西门C3，入场口令紫鹭928。"
)
HIT = {
    "id": 42,
    "title": "云舟活动",
    "source": "cloud-event.md",
    "chunk_index": 3,
    "content": CONTENT,
    "rrf_score": 0.03,
    "rerank_score": 0.8,
}


class ContextModel(GenericFakeChatModel):
    tool_name: str
    tool_inputs: list = Field(default_factory=list)
    verification_inputs: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "独立的回答验证员" in messages[0].content:
            payload = json.loads(messages[-1].content)
            self.verification_inputs.append(payload)
            assert all(fact in payload["draft"] for fact in FACTS)
            assert all(
                any(fact in evidence["output"] for evidence in payload["tool_evidence"])
                for fact in FACTS
            )
            response = AIMessage(content='{"passed":true,"issues":[],"revised_answer":""}')
        elif messages[-1].type == "tool":
            raw = messages[-1].content
            self.tool_inputs.append(raw)
            # Echo evidence, not prewritten facts: old 200-char tool output fails
            # the final-answer assertions even though retrieval returned CONTENT.
            response = AIMessage(content=f"工具实际提供的证据：{raw}")
        else:
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": {"query": messages[-1].content, "top_k": 5},
                        "id": "rag-call",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.mark.parametrize("mode", ["single", "multi"])
@pytest.mark.parametrize("scope", ["public", "private"])
async def test_完整工具结果到达单多图最终回答和核验(monkeypatch, mode, scope):
    assert CONTENT.index(FACTS[0]) > 200 and len(CONTENT) <= 300

    async def public_search(query, top_k):
        return [HIT]

    async def private_search(user_id, query, top_k):
        assert user_id == 17 and get_current_user_id() == 17
        return [HIT]

    monkeypatch.setattr(rag_service, "search", public_search)
    monkeypatch.setattr(user_knowledge_service, "search", private_search)

    @tool
    async def search_knowledge(query: str, top_k: int = 5) -> str:
        """Use the actual public MCP business function; transport is tested live."""
        return await mcp_business.search_knowledge(query, top_k)

    selected = search_knowledge if scope == "public" else local_tools.search_my_plans
    model = ContextModel(messages=iter(()), tool_name=selected.name)

    async def all_tools():
        return [selected]

    async def domain_tools():
        return {"knowledge" if scope == "public" else "itinerary": [selected]}

    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", all_tools)
    monkeypatch.setattr(multi_agent, "load_domain_tools", domain_tools)
    monkeypatch.setattr(agent, "_agent_instance", None)
    multi_agent.reset_cache()
    question = (
        "本地攻略里的云舟集合时间、入口和口令是什么？"
        if scope == "public"
        else "我上传的云舟资料里，行程集合时间、入口和口令是什么？"
    )
    try:
        result = await (_run_single if mode == "single" else _run_multi)(question, user_id=17)
        assert all(fact in result["answer"] for fact in FACTS)
        payload = json.loads(model.tool_inputs[0])
        assert payload["items"][0]["content"] == CONTENT
        assert payload["items"][0]["source"] == HIT["source"]
        assert payload["items"][0]["chunk_index"] == HIT["chunk_index"]
        assert payload["items"][0]["id"] == HIT["id"]
        assert not payload["context_incomplete"]
        if mode == "multi":
            assert model.verification_inputs
            assert not model.verification_inputs[0]["tool_evidence"][0]["truncated"]
    finally:
        multi_agent.reset_cache()


def test_整体预算保留完整高排名分块并明确省略低排名分块():
    hits = [
        {**HIT, "id": 1, "content": "优先资料\n" + "甲" * 300},
        {**HIT, "id": 2, "content": "次要资料\n" + "乙" * 300},
    ]
    output = format_knowledge_context(hits, scope="personal", max_chars=800)
    payload = json.loads(output)
    assert len(output) <= 800
    assert payload["included_count"] == payload["omitted_count"] == 1
    assert payload["retrieved_count"] == 2 and payload["context_incomplete"]
    assert payload["items"][0]["content"] == hits[0]["content"]
    assert "次要资料" not in output
    assert "不能断言原资料没有" in payload["notice"]


def test_单个超大分块不会被伪装成完整的部分正文():
    output = format_knowledge_context(
        [{**HIT, "content": "正文" * MAX_KNOWLEDGE_TOOL_CHARS}], scope="public"
    )
    payload = json.loads(output)
    assert payload["items"] == []
    assert payload["omitted_count"] == 1 and payload["context_incomplete"]
    assert len(output) <= MAX_KNOWLEDGE_TOOL_CHARS


def test_十个默认大小分块连同元数据都能完整进入工具与核验():
    hits = [{**HIT, "id": i, "chunk_index": i, "content": CONTENT} for i in range(10)]
    output = format_knowledge_context(hits, scope="public")
    payload = json.loads(output)
    assert payload["included_count"] == 10 and not payload["context_incomplete"]
    assert all(chunk["content"] == CONTENT for chunk in payload["items"])
    record = evidence_record("search_knowledge", {"query": "云舟活动"}, output)
    assert not record["truncated"] and record["output"] == output


async def test_MCP文本块不会因额外字符串转义造成核验证据截断():
    output = format_knowledge_context(
        [{**HIT, "content": "甲" * 5400 + "\n16:47 西门C3 紫鹭928"}], scope="public"
    )
    assert 5500 < len(output) <= MAX_KNOWLEDGE_TOOL_CHARS

    class MCPTextTool:
        name = "search_knowledge"

        async def ainvoke(self, args):
            return [{"type": "text", "text": output}]

    actual = await multi_agent._invoke_tool(
        [MCPTextTool()], {"name": "search_knowledge", "args": {"query": "云舟"}}
    )
    assert actual == output
    assert json.loads(actual)["items"][0]["content"].endswith("紫鹭928")
    record = evidence_record("search_knowledge", {"query": "云舟"}, actual)
    assert record["output"] == output and not record["truncated"]
