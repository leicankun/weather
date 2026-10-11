"""会话短期滚动摘要。

Revision ID: 0017_chat_summaries
Revises: 0016_chat_memory
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_chat_summaries"
down_revision = "0016_chat_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_summaries",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "covered_message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("last_checked_turn", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("chat_summaries")
