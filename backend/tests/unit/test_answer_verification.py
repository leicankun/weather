"""验证真实图中的证据交接、有限修正、失败关闭及 SSE 草稿隔离。"""

import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from pydantic import Field, ValidationError

from app.services import multi_agent
from app.services.answer_verification import (
    VERIFICATION_FAILED,
    VERIFICATION_UNAVAILABLE,
    AnswerReview,
    build_verification_prompt,
    evidence_record,
)
from app.services.user_context import get_current_user_id


def verdict(passed=True, issues=None, revised=""):
    return json.dumps(
        {"passed": passed, "issues": issues or [], "revised_answer": revised}, ensure_ascii=False
    )


class ReviewModel(GenericFakeChatModel):
    reviews: list[str] = Field(default_factory=list)
    payloads: list[dict] = Field(default_factory=list)
    domain_inputs: list[list] = Field(default_factory=list)
    fail_review: bool = False
    fail_merge: bool = False
    fail_after_tool: bool = False

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        # GenericFakeChatModel 默认分词流不保留工具调用，显式模拟完整 chunk。
        response = self._generate(messages).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=response.content,
                tool_calls=response.tool_calls,
                usage_metadata=response.usage_metadata,
            )
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = messages[0].content
        if "独立的回答验证员" in system:
            self.payloads.append(json.loads(messages[-1].content))
            if self.fail_review:
                raise RuntimeError("核验服务失败")
            content = self.reviews.pop(0)
            response = AIMessage(content=content)
        elif "主笔" in system:
            if self.fail_merge:
                raise RuntimeError("汇总失败")
            response = AIMessage(content="错误草稿：明天阴，打车9元。")
        else:
            self.domain_inputs.append(messages)
            if messages[-1].type == "tool":
                if self.fail_after_tool:
                    raise RuntimeError("工具之后模型失败")
                response = AIMessage(content="错误草稿：明天阴，打车9元。")
            else:
                name = "get_forecast" if "天气查询助手" in system else "recommend_outfit"
                response = AIMessage(
                    content="领域中间消息",
                    tool_calls=[{"name": name, "args": {"city": "广州"}, "id": name}],
                )
        response.usage_metadata = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.fixture(autouse=True)
def fresh_graph():
    multi_agent.reset_cache()
    yield
    multi_agent.reset_cache()


async def install_graph(monkeypatch, model, *, parallel=False):
    started = set()
    ready = asyncio.Event()

    async def synchronize(name):
        assert get_current_user_id() == 17
        started.add(name)
        if len(started) == 2:
            ready.set()
        if parallel:
            await asyncio.wait_for(ready.wait(), timeout=2)

    @tool
    async def get_forecast(city: str) -> str:
        """查询预报。"""
        await synchronize("weather")
        return "工具原始预报：广州明天晴，21.6~32.6℃。"

    @tool
    async def recommend_outfit(city: str) -> str:
        """查询穿搭。"""
        await synchronize("outfit")
        return "工具原始穿搭：广州今天阴，最高31.2℃；短袖。"

    async def loader():
        return {"weather": [get_forecast], "outfit": [recommend_outfit]}

    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    monkeypatch.setattr(multi_agent, "load_domain_tools", loader)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("parallel", [False, True])
async def test_真实图仅发布复核后的答案并合并工具证据(monkeypatch, stream, parallel):
    corrected = "明天广州晴，21.6~32.6℃，建议短袖并带薄外套；未查到实际打车费用。"
    model = ReviewModel(
        messages=iter(()),
        reviews=[verdict(False, ["日期混用", "无打车报价证据"], corrected), verdict()],
    )
    await install_graph(monkeypatch, model, parallel=parallel)
    question = "广州明天天气，怎么穿" if parallel else "广州明天天气"
    history = [{"role": "user", "content": "我怕冷"}]
    if stream:
        events = [
            event
            async for event in multi_agent.chat_multi_stream(
                question, 17, history=history, memory_context="用户常住海珠区"
            )
        ]
        assert events == [{"type": "token", "content": corrected}, {"type": "done"}]
    else:
        result = await multi_agent.chat_multi(
            question, 17, history=history, memory_context="用户常住海珠区"
        )
        assert result["answer"] == corrected
        assert result["verification"] == {
            "status": "corrected",
            "checks": 2,
            "issues": ["日期混用", "无打车报价证据"],
        }
        assert result["metrics"]["llm_calls"] == (7 if parallel else 4)
        assert result["metrics"]["prompt_tokens"] == (21 if parallel else 12)
    assert len(model.payloads) == 2
    first, second = model.payloads
    assert first["question"] == question and first["history"] == history
    assert first["memory_context"] == "用户常住海珠区"
    records = {record["tool"]: record for record in first["tool_evidence"]}
    assert "21.6~32.6" in records["get_forecast"]["output"]
    assert json.loads(records["get_forecast"]["arguments"]) == {
        "city": "广州",
        "target_date": first["temporal_context"]["dates"][0],
    }
    if parallel:
        assert set(records) == {"get_forecast", "recommend_outfit"}
        # 原始工具消息仍各域私有；仅验证节点拿到两域证据。
        for messages in model.domain_inputs:
            outputs = [message.content for message in messages if message.type == "tool"]
            assert len(outputs) <= 1
    assert second["draft"] == corrected and second["tool_evidence"] == first["tool_evidence"]
    assert second["domain_answers"] == {}  # 复查不携带已经被修正的领域原稿。
    assert get_current_user_id() is None


@pytest.mark.parametrize("kind", ["failure", "bad_json", "contradiction", "empty"])
async def test_无法核验或两次不通过不会发布草稿(monkeypatch, kind):
    review = verdict(False, ["仍缺证据"], "仍然错误的修正稿")
    model = ReviewModel(
        messages=iter(()),
        reviews={
            "failure": [],
            "bad_json": ["普通文本"],
            "contradiction": [review, review],
            "empty": ['{"passed":false,"issues":["错误"],"revised_answer":""}'],
        }[kind],
        fail_review=kind == "failure",
    )
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)
    result = await multi_agent._verify_node({"question": "明天天气", "answer": "错误草稿"})
    assert result["answer"] == (
        VERIFICATION_FAILED if kind == "contradiction" else VERIFICATION_UNAVAILABLE
    )
    assert result["verification"]["status"] == (
        "failed" if kind == "contradiction" else "unverified"
    )
    assert result["metrics"]["llm_calls"] == (2 if kind == "contradiction" else 1)


async def test_汇总异常的拼接稿仍接受核验(monkeypatch):
    model = ReviewModel(messages=iter(()), reviews=[verdict()], fail_merge=True)
    await install_graph(monkeypatch, model, parallel=True)
    result = await multi_agent.chat_multi("广州明天天气怎么穿", 17)
    assert result["verification"]["status"] == "passed"
    assert len(model.payloads) == 1
    assert len(model.payloads[0]["tool_evidence"]) == 2


async def test_工具之后模型异常仍保留证据(monkeypatch):
    model = ReviewModel(messages=iter(()), reviews=[verdict()], fail_after_tool=True)
    await install_graph(monkeypatch, model)
    result = await multi_agent.chat_multi("广州明天天气", 17)
    assert result["verification"]["status"] == "passed"
    assert "暂时查询失败" in model.payloads[0]["draft"]
    assert "32.6" in model.payloads[0]["tool_evidence"][0]["output"]
    assert result["metrics"]["tool_calls"] == 1
    assert result["metrics"]["llm_calls"] == 3


@pytest.mark.parametrize("failure", [True, False])
async def test_SSE验证失败仅发送失败提示(monkeypatch, failure):
    bad = verdict(False, ["没有证据"], "错误修正稿")
    model = ReviewModel(messages=iter(()), reviews=[bad, bad], fail_review=failure)
    await install_graph(monkeypatch, model)
    events = [event async for event in multi_agent.chat_multi_stream("广州明天天气", 17)]
    assert events == [
        {"type": "token", "content": VERIFICATION_UNAVAILABLE if failure else VERIFICATION_FAILED},
        {"type": "done"},
    ]
    assert get_current_user_id() is None


def test_证据预算与截断显式标记():
    record = evidence_record("web_search", {"query": "甲" * 3000}, "乙" * 7000)
    assert record["truncated"] and len(record["output"]) == 6000
    payload = json.loads(
        build_verification_prompt(
            {"question": "天气", "domain_evidence": {"knowledge": [record] * 10}}, "草稿"
        )
    )
    assert payload["omitted_evidence_count"] > 0
    assert all(item["truncated"] for item in payload["tool_evidence"])
    assert len(json.dumps(payload, ensure_ascii=False)) < 35000


@pytest.mark.parametrize(
    "payload",
    [
        {"passed": "true", "issues": [], "revised_answer": ""},
        {"passed": True, "issues": ["冲突"], "revised_answer": ""},
        {"passed": False, "issues": [], "revised_answer": "新答案"},
        {"passed": True},
    ],
)
def test_拒绝不完整或互相矛盾的判定(payload):
    with pytest.raises(ValidationError):
        AnswerReview.model_validate(payload)
