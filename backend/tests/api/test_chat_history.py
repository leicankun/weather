"""真实数据库和完整多智能体图验证多轮对话；模型替身禁止外部调用。"""

import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User
from app.services import chat_history_service, multi_agent


class HistoryModel(GenericFakeChatModel):
    calls: list = Field(default_factory=list)
    route_prompts: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        prior_users = [message.content for message in messages[1:-1] if message.type == "human"]
        answer = f"前文：{prior_users[-1]}" if prior_users else "已记录本轮信息"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])


@pytest.fixture
def multi_model(monkeypatch):
    multi_agent.reset_cache()
    model = HistoryModel(messages=iter(()))
    monkeypatch.setattr(settings, "AGENT_MODE", "multi")
    monkeypatch.setattr(multi_agent, "get_llm", lambda: model)

    async def tools():
        return {}

    async def route(*args, **kwargs):
        model.route_prompts.append(args[0])
        return multi_agent.DomainChoice(domains=[])

    monkeypatch.setattr(multi_agent, "load_domain_tools", tools)
    monkeypatch.setattr(multi_agent, "ainvoke_json", route)
    yield model
    multi_agent.reset_cache()


async def _ask(client, path, message, conversation_id, headers=None):
    response = await client.post(
        path, json={"message": message, "conversation_id": conversation_id}, headers=headers
    )
    assert response.status_code == 200
    if path.endswith("/stream"):
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
        return "".join(event.get("content", "") for event in events if event["type"] == "token")
    return response.json()["data"]["answer"]


@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
@pytest.mark.parametrize("follow_up", ["那怎么走", "那穿什么、怎么走", "继续"])
async def test_多智能体两轮对话从数据库读取上下文(
    client, auth_headers, db, multi_model, path, follow_up
):
    conversation_id = "multi-history"
    first = "我周末去广州塔，预算800元"
    await _ask(client, path, first, conversation_id, auth_headers)
    multi_model.calls.clear()
    multi_model.route_prompts.clear()
    answer = await _ask(client, path, follow_up, conversation_id, auth_headers)
    assert first in answer
    if follow_up == "继续":
        # 空路由也须复用，避免流式转发节点与重新路由的结果不一致。
        assert len(multi_model.route_prompts) == 1
        assert first in multi_model.route_prompts[0]
        assert multi_model.route_prompts[0].endswith("本次问题：继续")
    for messages in multi_model.calls:
        assert [message.type for message in messages] == ["system", "human", "ai", "human"]
        assert messages[1].content == first
        assert messages[2].content == "已记录本轮信息"
        assert follow_up in messages[-1].content
    owner_id = await db.scalar(select(User.id).where(User.username == "tester"))
    saved = await chat_history_service.get_conversation_messages(owner_id, conversation_id, db=db)
    assert [message["role"] for message in saved] == ["user", "assistant", "user", "assistant"]
    assert saved[-1]["content"] == answer


@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
async def test_仅取最近十条并隔离会话用户和匿名请求(client, auth_headers, db, multi_model, path):
    owner_id = await db.scalar(select(User.id).where(User.username == "tester"))
    other = User(username="other_chatter", password_hash="unused")
    db.add(other)
    await db.commit()
    for index in range(12):
        await chat_history_service.add_message(
            owner_id, "shared", "user" if index % 2 == 0 else "assistant", f"历史{index}", db=db
        )
    await chat_history_service.add_message(other.id, "shared", "user", "他人的历史", db=db)
    await chat_history_service.add_message(owner_id, "different", "user", "别的会话", db=db)

    await _ask(client, path, "那怎么走", "shared", auth_headers)
    assert [message.content for message in multi_model.calls[-1][1:-1]] == [
        f"历史{index}" for index in range(2, 12)
    ]
    await _ask(client, path, "那怎么走", "new", auth_headers)
    assert len(multi_model.calls[-1]) == 2
    other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    await _ask(client, path, "那怎么走", "shared", other_headers)
    assert [message.content for message in multi_model.calls[-1][1:-1]] == ["他人的历史"]
    await _ask(client, path, "那怎么走", "shared")
    assert len(multi_model.calls[-1]) == 2
    assert await chat_history_service.get_recent_history(None, "shared", db=db) == []
