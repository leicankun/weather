"""RAG 知识库服务：向量入库与 LangChain 混合检索。

- build_index(): 扫描 knowledge_base 文档 -> 分块 -> embedding -> 写入 knowledge_chunks（upsert）
- search(): BM25 + pgvector 召回 -> RRF 融合 -> gte-rerank-v2 重排 -> TopK
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.knowledge import KnowledgeChunk
from app.services.embedding_client import embedding_client
from app.services.hybrid_retrieval import search_hybrid
from app.services.knowledge_loader import load_documents


def _make_session() -> async_sessionmaker[AsyncSession]:
    """Celery 任务用的独立 NullPool 引擎，避免跨事件循环复用连接池。"""
    engine = create_async_engine(settings.DB_URL, poolclass=NullPool, pool_pre_ping=True)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def _store_chunks(
    session_factory: async_sessionmaker[AsyncSession],
    rows: list[dict[str, Any]],
) -> int:
    """按 (source, chunk_index) 去重写入向量块。"""
    async with session_factory() as db:
        stmt = pg_insert(KnowledgeChunk).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "chunk_index"],
            set_={
                "title": stmt.excluded.title,
                "content": stmt.excluded.content,
                "embedding": stmt.excluded.embedding,
                "meta": stmt.excluded.meta,
            },
        )
        await db.execute(stmt)
        await db.commit()
    return len(rows)


async def build_index(use_celery_engine: bool = False) -> dict[str, Any]:
    """全量重建向量索引：加载文档 -> 分块 -> 生成向量 -> upsert 入库。

    返回统计信息（文档数、块数、向量化块数）。
    """
    chunks = load_documents()
    if not chunks:
        raise RuntimeError("知识库文档为空，请检查 knowledge_base 目录")

    # 批量生成向量（按 EMBED_BATCH_SIZE 分批，控制单次请求大小）
    texts = [c.content for c in chunks]
    vectors: list[list[float]] = []
    batch = settings.EMBED_BATCH_SIZE
    for i in range(0, len(texts), batch):
        vectors.extend(await embedding_client.embed_texts(texts[i : i + batch]))

    if len(vectors) != len(chunks):
        raise RuntimeError(f"向量数量 {len(vectors)} 与分块数量 {len(chunks)} 不一致")

    rows = [
        {
            "title": c.title,
            "source": c.source,
            "content": c.content,
            "chunk_index": c.chunk_index,
            "embedding": vectors[i],
            "meta": {"dim": len(vectors[i])},
        }
        for i, c in enumerate(chunks)
    ]

    factory = _make_session() if use_celery_engine else AsyncSessionLocal
    stored = await _store_chunks(factory, rows)

    # 校验维度与库中数据
    async with factory() as db:
        sample = await db.execute(text("SELECT count(*) FROM knowledge_chunks"))
        total = sample.scalar()

    return {
        "documents": len({c.source for c in chunks}),
        "chunks": len(chunks),
        "stored": stored,
        "total_in_db": total,
        "embedding_mode": "api" if embedding_client.available else "local_fallback",
    }


async def search(
    query: str,
    top_k: int | None = None,
    use_celery_engine: bool = False,
) -> list[dict[str, Any]]:
    """LangChain 双路召回、RRF 和重排；similarity 保留余弦相似度含义。"""
    top_k = settings.EMBED_TOP_K if top_k is None else top_k
    if not query.strip():
        raise ValueError("查询文本不能为空")

    factory = _make_session() if use_celery_engine else AsyncSessionLocal
    async with factory() as db:
        return await search_hybrid(db, query, top_k)
