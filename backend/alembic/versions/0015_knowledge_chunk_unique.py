"""补齐公共知识库 upsert 所需的唯一约束，保留已有数据及 ORM 已建约束。

Revision ID: 0015_knowledge_chunk_unique
Revises: 0014_missing_business_tables
"""

from alembic import op

revision = "0015_knowledge_chunk_unique"
down_revision = "0014_missing_business_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 早期 create_all 已建同名约束时跳过；若旧数据重复，由数据库报错，
    # 事务回滚并保留数据，不在迁移中静默删除知识内容。
    op.execute(
        """
        DO $migration$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'knowledge_chunks'::regclass
                  AND conname = 'uq_knowledge_source_chunk'
            ) THEN
                ALTER TABLE knowledge_chunks
                ADD CONSTRAINT uq_knowledge_source_chunk UNIQUE (source, chunk_index);
            END IF;
        END $migration$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_chunks DROP CONSTRAINT IF EXISTS uq_knowledge_source_chunk")
