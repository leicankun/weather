"""对比入口必须执行真实领域图，并向两种模式传递相同背景和用户身份。"""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from app.scripts.compare_agents import _run_multi, _run_single
from app.services import agent, multi_agent
from app.services.user_context import get_current_user_id


class ToolModel(GenericFakeChatModel):
    calls: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        response = (
            AIMessage(content="天气工具结果已收到")
            if messages[-1].type == "tool"
            else AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_weather",
                        "args": {"city": "广州"},
                        "id": "weather-call",
                        "type": "tool_call",
                    }
                ],
            )
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_比较真实领域执行并记录工具结果且共享相同上下文(monkeypatch, mode):
    model = ToolModel(messages=iter(()))

    @tool
    async def get_weather(city: str) -> str:
        """测试天气工具。"""
        assert city == "广州" and get_current_user_id() == 17
        return "测试气温23度"

    async def all_tools():
        return [get_weather]

    async def domain_tools():
        return {"weather": [get_weather]}

    monkeypatch.setattr(agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(agent, "_get_all_tools", all_tools)
    monkeypatch.setattr(multi_agent, "load_domain_tools", domain_tools)
    monkeypatch.setattr(agent, "_agent_instance", None)
    monkeypatch.setattr(multi_agent, "_graph", None)
    runner = _run_single if mode == "single" else _run_multi
    result = await runner(
        "广州天气怎么样",
        user_id=17,
        history=[{"role": "user", "content": "我怕冷"}],
        memory_context="常住海珠区",
    )
    assert result["answer"] == "天气工具结果已收到"
    assert result["llm"] == 2 and result["tools"] == 1
    assert result["tool_trace"][0]["name"] == "get_weather"
    assert result["tool_trace"][0]["output"] == "测试气温23度"
    assert "常住海珠区" in model.calls[0][0].content
    assert model.calls[0][1].content == "我怕冷"
    assert get_current_user_id() is None
    if mode == "multi":
        assert result["domains"] == ["weather"]
        assert result["domain_answers"] == {"weather": "天气工具结果已收到"}
