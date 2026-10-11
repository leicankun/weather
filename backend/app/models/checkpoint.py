"""应用线程归属与官方 PostgresSaver 3.1.2 的存储表。

官方 saver 负责快照编解码和读写；元数据注册让迁移、测试清表和用户级联删除一致。
"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentCheckpointThread(Base, TimestampMixin):
    __tablename__ = "agent_checkpoint_threads"
    __table_args__ = (UniqueConstraint("user_id", "conversation_id", "mode"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    request: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # 与消息在同一事务写入，恢复已结束的图时不会重复保存一轮。
    assistant_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )


def _thread_column():
    return Column(
        "thread_id",
        Text,
        ForeignKey("agent_checkpoint_threads.thread_id", ondelete="CASCADE"),
        primary_key=True,
    )


checkpoint_migrations = Table(
    "checkpoint_migrations", Base.metadata, Column("v", Integer, primary_key=True)
)
checkpoints = Table(
    "checkpoints",
    Base.metadata,
    _thread_column(),
    Column("checkpoint_ns", Text, primary_key=True, server_default=""),
    Column("checkpoint_id", Text, primary_key=True),
    Column("parent_checkpoint_id", Text),
    Column("type", Text),
    Column("checkpoint", JSONB, nullable=False),
    Column("metadata", JSONB, nullable=False, server_default="{}"),
)
checkpoint_blobs = Table(
    "checkpoint_blobs",
    Base.metadata,
    _thread_column(),
    Column("checkpoint_ns", Text, primary_key=True, server_default=""),
    Column("channel", Text, primary_key=True),
    Column("version", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("blob", LargeBinary),
)
checkpoint_writes = Table(
    "checkpoint_writes",
    Base.metadata,
    _thread_column(),
    Column("checkpoint_ns", Text, primary_key=True, server_default=""),
    Column("checkpoint_id", Text, primary_key=True),
    Column("task_id", Text, primary_key=True),
    Column("idx", Integer, primary_key=True),
    Column("channel", Text, nullable=False),
    Column("type", Text),
    Column("blob", LargeBinary, nullable=False),
    Column("task_path", Text, nullable=False, server_default=""),
)

for _table in (checkpoints, checkpoint_blobs, checkpoint_writes):
    Index(f"{_table.name}_thread_id_idx", _table.c.thread_id)
