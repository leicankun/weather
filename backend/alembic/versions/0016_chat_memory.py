"""双层聊天记忆。

Revision ID: 0016_chat_memory
Revises: 0015_knowledge_chunk_unique
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0016_chat_memory"
down_revision = "0015_knowledge_chunk_unique"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "memory_states",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("forget_before_id", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "memory_cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column(
            "source_message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *timestamps(),
        sa.UniqueConstraint("user_id", "kind", "key", name="uq_memory_card_key"),
    )
    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("message_id", "chunk_index", name="uq_memory_episode_chunk"),
    )
    for table, columns in (
        ("memory_cards", ("user_id", "source_message_id")),
        ("memory_episodes", ("user_id", "message_id", "conversation_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("memory_episodes")
    op.drop_table("memory_cards")
    op.drop_table("memory_states")
