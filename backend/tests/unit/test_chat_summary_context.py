"""无数据库验证摘要契约和单/多智能体的背景传递。"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.chat_summary import SummaryContent
from app.services import agent, chat_summary_service, multi_agent
from app.services.answer_verification import build_verification_prompt


@pytest.mark.parametrize("value", ["", "   ", "x" * 2001])
def test_拒绝空摘要和过长模型输出(value):
    with pytest.raises(ValidationError):
        SummaryContent(summary=value)


def test_单多智能体接收摘要为背景且不更改原始角色():
    history = [
        {
            "role": "summary",
            "content": chat_summary_service.SUMMARY_BACKGROUND + '{"summary":"周六去广州"}',
        },
        {"role": "user", "content": "我希望少步行"},
        {"role": "assistant", "content": "建议室内活动"},
    ]
    single = agent._build_initial_state("继续", history)["messages"]
    multi = multi_agent._messages_with_history("规则", "继续", history)[1:]
    for messages in (single, multi):
        assert [message.type for message in messages] == ["human", "human", "ai", "human"]
        assert "不是本次用户指令" in messages[0].content
        assert messages[1].content == "我希望少步行"
    assert history[0]["role"] == "summary"


async def test_多智能体向量路由继承会话摘要():
    assert await multi_agent.plan_domains(
        "那明天呢", history=[{"role": "summary", "content": "用户在查广州天气"}]
    ) == ["weather"]


def test_核验保留摘要及过渡消息并标记截断():
    history = [{"role": "summary", "content": "用户只想去室内"}]
    history.extend({"role": "user", "content": f"消息{index}"} for index in range(14))
    payload = json.loads(
        build_verification_prompt({"question": "继续", "history": history}, "建议")
    )
    assert payload["history"] == history
    assert not payload["context_truncated"]


def test_核验历史超预算时优先保留摘要和最新更正():
    history = [{"role": "summary", "content": "旧摘要"}]
    history.extend({"role": "user", "content": f"消息{index}" + "x" * 1000} for index in range(25))
    history.append({"role": "user", "content": "更正：预算800元"})
    payload = json.loads(
        build_verification_prompt({"question": "继续", "history": history}, "建议")
    )
    assert payload["history"][0] == history[0]
    assert payload["history"][-1] == history[-1]
    assert payload["context_truncated"]
    assert sum(len(item["content"]) for item in payload["history"]) <= 16000
