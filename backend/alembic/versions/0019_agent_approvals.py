"""Sensitive action approval and execution receipt.

Revision ID: 0019_agent_approvals
Revises: 0018_agent_checkpoints
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0019_agent_approvals"
down_revision = "0018_agent_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "thread_id",
            sa.Text(),
            sa.ForeignKey("agent_checkpoint_threads.thread_id", ondelete="SET NULL"),
        ),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("actions", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("thread_id", "run_id"),
    )
    op.create_index("ix_agent_approvals_user_id", "agent_approvals", ["user_id"])
    op.create_index("ix_agent_approvals_conversation_id", "agent_approvals", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("agent_approvals")
