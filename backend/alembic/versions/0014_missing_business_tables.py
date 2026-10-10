"""补齐早期仅由 create_all 创建的业务表。

Revision ID: 0014_missing_business_tables
Revises: 0013_user_preferences

兼容已经使用 init_db/create_all 的数据库：保留现有表和数据，补齐索引。
表结构固定在迁移中，避免未来 ORM 模型变化影响旧版本迁移。
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_missing_business_tables"
down_revision = "0013_user_preferences"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "user_knowledge",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        if_not_exists=True,
    )
    op.create_index("ix_user_knowledge_user_id", "user_knowledge", ["user_id"], if_not_exists=True)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        *_timestamps(),
        if_not_exists=True,
    )
    op.create_index("ix_chat_messages_user_id", "chat_messages", ["user_id"], if_not_exists=True)
    op.create_index(
        "ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"], if_not_exists=True
    )

    op.create_table(
        "itineraries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("activity", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        if_not_exists=True,
    )
    op.create_index("ix_itineraries_user_id", "itineraries", ["user_id"], if_not_exists=True)
    op.create_index("ix_itineraries_date", "itineraries", ["date"], if_not_exists=True)

    op.create_table(
        "clean_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("cleaned_rows", sa.Integer(), nullable=True),
        sa.Column("duplicated_removed", sa.Integer(), nullable=True),
        sa.Column("filled_missing", sa.Integer(), nullable=True),
        sa.Column("filtered_outliers", sa.Integer(), nullable=True),
        sa.Column("unit_standardized", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(64), nullable=True),
        *_timestamps(),
        if_not_exists=True,
    )
    op.create_index(
        "ix_clean_tasks_task_id", "clean_tasks", ["task_id"], unique=True, if_not_exists=True
    )


def downgrade() -> None:
    for name in ("clean_tasks", "itineraries", "chat_messages", "user_knowledge"):
        op.drop_table(name)
