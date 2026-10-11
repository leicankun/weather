"""LangGraph PostgreSQL checkpoints and authenticated thread ownership.

Revision ID: 0018_agent_checkpoints
Revises: 0017_chat_summaries
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0018_agent_checkpoints"
down_revision = "0017_chat_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen schema matching official PostgresSaver 3.1.2 migrations 0..9.
    op.create_table(
        "agent_checkpoint_threads",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("request", JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "assistant_message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "conversation_id", "mode"),
    )
    op.create_index("ix_agent_checkpoint_threads_user_id", "agent_checkpoint_threads", ["user_id"])
    op.create_index(
        "ix_agent_checkpoint_threads_conversation_id",
        "agent_checkpoint_threads",
        ["conversation_id"],
    )
    op.create_table("checkpoint_migrations", sa.Column("v", sa.Integer(), primary_key=True))
    op.create_table(
        "checkpoints",
        _thread_column(),
        _namespace_column(),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("parent_checkpoint_id", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("checkpoint", JSONB(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "checkpoint_blobs",
        _thread_column(),
        _namespace_column(),
        sa.Column("channel", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary()),
    )
    op.create_table(
        "checkpoint_writes",
        _thread_column(),
        _namespace_column(),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("idx", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False, server_default=""),
    )
    for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.create_index(f"{name}_thread_id_idx", name, ["thread_id"])
    op.execute(
        sa.text(
            "INSERT INTO checkpoint_migrations(v) SELECT generate_series(0, 9) "
            "ON CONFLICT DO NOTHING"
        )
    )


def _thread_column():
    return sa.Column(
        "thread_id",
        sa.Text(),
        sa.ForeignKey("agent_checkpoint_threads.thread_id", ondelete="CASCADE"),
        primary_key=True,
    )


def _namespace_column():
    return sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default="")


def downgrade() -> None:
    for name in (
        "checkpoint_writes",
        "checkpoint_blobs",
        "checkpoints",
        "checkpoint_migrations",
        "agent_checkpoint_threads",
    ):
        op.drop_table(name)
