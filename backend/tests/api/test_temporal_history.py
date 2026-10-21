"""Real PostgreSQL timestamps must anchor relative dates across chat days."""

from datetime import UTC, date, datetime

import pytest

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.user import User
from app.services import chat_summary_service
from app.services.temporal_context import build_temporal_context


@pytest.mark.parametrize("summary_enabled", [False, True])
async def test_history_keeps_source_day_for_follow_up(db, monkeypatch, summary_enabled):
    monkeypatch.setattr(settings, "CHAT_SUMMARY_ENABLED", summary_enabled)
    user = User(username=f"temporal_{summary_enabled}", password_hash="fixture")
    db.add(user)
    await db.flush()
    db.add(
        ChatMessage(
            user_id=user.id,
            conversation_id="cross-day",
            role="user",
            content="明天去广州塔",
            created_at=datetime(2026, 10, 20, 14, tzinfo=UTC),
        )
    )
    await db.commit()
    history = await chat_summary_service.get_context(user.id, "cross-day")
    assert history[0]["date_reference"] == "2026-10-20"
    context = build_temporal_context("那我穿什么", history, date(2026, 10, 21))
    assert context["dates"] == ["2026-10-21"]
