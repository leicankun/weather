"""用户长期记忆：JSON 卡片、带来源的历史切片，以及清空水位。"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MemoryCard(Base, TimestampMixin):
    __tablename__ = "memory_cards"
    __table_args__ = (UniqueConstraint("user_id", "kind", "key", name="uq_memory_card_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB)
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )


class MemoryEpisode(Base, TimestampMixin):
    __tablename__ = "memory_episodes"
    __table_args__ = (
        UniqueConstraint("message_id", "chunk_index", name="uq_memory_episode_chunk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)


class MemoryState(Base):
    """清空记忆不删除聊天；水位防止回填或在途任务重新记住旧消息。"""

    __tablename__ = "memory_states"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    forget_before_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
