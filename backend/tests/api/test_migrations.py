"""直接迁移空库，防止 ORM create_all 掩盖漏建表的问题。"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.models.user_knowledge import UserKnowledge
from app.services import rag_service
from app.services.embedding_client import embedding_client
from tests.conftest import TEST_DB_URL

BACKEND_DIR = Path(__file__).resolve().parents[2]
MISSING_TABLES = (
    "user_knowledge",
    "chat_messages",
    "itineraries",
    "clean_tasks",
    "memory_cards",
    "memory_episodes",
    "memory_states",
    "chat_summaries",
    "agent_checkpoint_threads",
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "agent_approvals",
)


async def _upgrade(url, revision="head"):
    env = {**os.environ, "DB_URL": url.render_as_string(hide_password=False)}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        revision,
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
    assert process.returncode == 0, output.decode(errors="replace")


@pytest_asyncio.fixture
async def migration_database(db):
    # db 夹具让 --no-db 明确跳过；所有迁移只作用于新建的独立临时库。
    name = f"weather_migration_{uuid4().hex}_test"
    assert name.startswith("weather_migration_") and name.endswith("_test")
    url = make_url(TEST_DB_URL).set(database=name)
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    created = False
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        yield url
    finally:
        if created:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}"'))
        await admin.dispose()


async def test_fresh_migrations_create_models_and_support_knowledge_api(
    migration_database, client, monkeypatch
):
    await _upgrade(migration_database)
    engine = create_async_engine(migration_database, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def database_override():
        async with sessions() as session:
            yield session

    async def fake_embeddings(texts):
        return [[1.0, *([0.0] * 1023)] for _ in texts]

    monkeypatch.setattr(embedding_client, "embed_texts", fake_embeddings)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database_override
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
            assert set(Base.metadata.tables) <= set(tables)
            for name in MISSING_TABLES:
                columns = await conn.run_sync(lambda c, name=name: inspect(c).get_columns(name))
                assert set(Base.metadata.tables[name].columns.keys()) == {
                    column["name"] for column in columns
                }
            constraints = await conn.run_sync(
                lambda c: inspect(c).get_unique_constraints("knowledge_chunks")
            )
            assert any(
                constraint["name"] == "uq_knowledge_source_chunk"
                and constraint["column_names"] == ["source", "chunk_index"]
                for constraint in constraints
            )

        row = {
            "title": "公共迁移回归",
            "source": "migration-test.md",
            "chunk_index": 0,
            "content": "原始内容",
            "embedding": [1.0, *([0.0] * 1023)],
            "meta": {"version": 1},
        }
        await rag_service._store_chunks(sessions, [row])
        updated = {**row, "content": "更新内容", "meta": {"version": 2}}
        await rag_service._store_chunks(sessions, [updated])
        async with sessions() as session:
            saved = (
                await session.execute(text("SELECT content, meta FROM knowledge_chunks"))
            ).one()
            assert saved.content == "更新内容" and saved.meta == {"version": 2}

        # 两个独立会话同时 upsert 同一来源分块，唯一约束防止并发重复插入。
        first = {**row, "chunk_index": 1, "content": "版本A", "meta": {"version": "A"}}
        second = {**row, "chunk_index": 1, "content": "版本B", "meta": {"version": "B"}}
        await asyncio.gather(
            rag_service._store_chunks(sessions, [first]),
            rag_service._store_chunks(sessions, [second]),
        )
        async with sessions() as session:
            saved = (
                await session.execute(
                    text("SELECT content, meta FROM knowledge_chunks WHERE chunk_index = 1")
                )
            ).all()
            assert len(saved) == 1
            assert saved[0].content == f"版本{saved[0].meta['version']}"

        async with sessions() as session:
            owner = User(username="migration_owner", password_hash="unused")
            other = User(username="migration_other", password_hash="unused")
            session.add_all([owner, other])
            await session.commit()
            headers = {"Authorization": f"Bearer {create_access_token(owner.id)}"}
            other_headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}

        response = await client.get("/api/v1/my-knowledge", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["items"] == []
        response = await client.post(
            "/api/v1/my-knowledge",
            headers=headers,
            json={"title": "迁移回归攻略", "content": "下雨时去博物馆。"},
        )
        assert response.status_code == 201
        response = await client.get("/api/v1/my-knowledge", headers=headers)
        assert response.json()["data"]["items"][0]["title"] == "迁移回归攻略"
        response = await client.post(
            "/api/v1/my-knowledge/search", headers=headers, json={"query": "下雨去哪"}
        )
        assert response.status_code == 200
        assert response.json()["data"]["items"][0]["content"] == "下雨时去博物馆。"
        response = await client.get("/api/v1/my-knowledge", headers=other_headers)
        assert response.json()["data"]["items"] == []
        response = await client.delete("/api/v1/my-knowledge/迁移回归攻略", headers=headers)
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


async def test_upgrade_preserves_tables_previously_created_by_orm(migration_database):
    await _upgrade(migration_database, "0013_user_preferences")
    engine = create_async_engine(migration_database, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # 这些是 0013 时期曾由 ORM 建立的旧表；记忆表由 0016 正常创建。
            for name in MISSING_TABLES[:4]:
                await conn.run_sync(lambda c, name=name: Base.metadata.tables[name].create(c))
            await conn.execute(
                text(
                    "ALTER TABLE knowledge_chunks ADD CONSTRAINT uq_knowledge_source_chunk "
                    "UNIQUE (source, chunk_index)"
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            owner = User(username="legacy_owner", password_hash="unused")
            session.add(owner)
            await session.flush()
            session.add(UserKnowledge(user_id=owner.id, title="已有资料", content="保留原内容"))
            await session.commit()

        await _upgrade(migration_database)
        async with sessions() as session:
            saved = await session.scalar(select(UserKnowledge))
            assert saved.title == "已有资料"
            assert saved.content == "保留原内容"
    finally:
        await engine.dispose()


async def test_migrated_schema_supports_official_checkpoint_resume(migration_database, monkeypatch):
    from langchain_core.messages import AIMessage

    from app.core.config import settings
    from app.services import agent, checkpoint_service

    await _upgrade(migration_database)
    engine = create_async_engine(migration_database, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "CHECKPOINT_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_MODE", "single")
    monkeypatch.setattr(
        settings, "DB_URL", migration_database.render_as_string(hide_password=False)
    )
    monkeypatch.setattr(checkpoint_service, "AsyncSessionLocal", sessions)

    async def tools():
        return []

    async def classify(state):
        return {"intent": "other"}

    async def answer(state):
        return {"messages": [AIMessage(content="migration answer")], "answer": "migration answer"}

    monkeypatch.setattr(agent, "_get_all_tools", tools)
    monkeypatch.setattr(agent, "_classify_intent", classify)
    monkeypatch.setattr(agent, "_agent_node", answer)
    try:
        async with sessions() as db:
            user = User(username="checkpoint_migration", password_hash="unused")
            db.add(user)
            await db.commit()
        async with checkpoint_service.open_thread(user.id, "migration", "single"):
            assert (await agent.chat("hello", user_id=user.id))["answer"] == "migration answer"
            await checkpoint_service.save_turn("hello", "migration answer")
        async with checkpoint_service.open_thread(user.id, "migration", "single", resume=True):
            assert (await agent.chat("", user_id=user.id))["answer"] == "migration answer"
        async with sessions() as db:
            assert await db.scalar(text("SELECT count(*) FROM checkpoint_migrations")) == 10
    finally:
        await engine.dispose()
