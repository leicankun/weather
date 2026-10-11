"""真实分块/API 验收：临时用户上传、检索、删除，调用已配置的真实模型。

backend 中运行 python -m app.scripts.verify_chunking --output ../docs/CHUNKING_LIVE_2026-10-11_FINAL.json
拒绝覆盖已有报告；不更改现有用户文档或公共索引。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.user import User
from app.models.user_knowledge import UserKnowledge
from app.services.embedding_client import embedding_client
from app.services.knowledge_loader import load_documents
from app.services.rerank_client import rerank_client


class FallbackObserver(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


async def verify(output: Path) -> None:
    if output.exists():
        raise FileExistsError("报告已存在，请更换输出路径")
    if not embedding_client.available or not rerank_client.available or not settings.RERANK_ENABLED:
        raise RuntimeError("真实验收需要配置 Embedding 并启用 rerank")
    observer = FallbackObserver()
    loggers = [
        logging.getLogger(f"app.services.{name}") for name in ("embedding_client", "rerank_client")
    ]
    for logger in loggers:
        logger.addHandler(observer)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "transport": "FastAPI ASGI + real PostgreSQL/model APIs",
        "embedding": embedding_client.model,
        "rerank": rerank_client.model,
        "cleaned_up": False,
    }
    user_id = None
    title = f"chunking-live-{uuid4().hex[:10]}"
    try:
        corpus = load_documents()
        report["public_corpus"] = {
            "documents": len({c.source for c in corpus}),
            "chunks": len(corpus),
            "min_chars": min(len(c.content) for c in corpus),
            "max_chars": max(len(c.content) for c in corpus),
            "per_file": dict(Counter(c.source for c in corpus)),
        }
        async with AsyncSessionLocal() as db:
            user = User(username=title, password_hash="unused")
            db.add(user)
            await db.flush()
            user_id = user.id
            await db.commit()
        headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
        sections = [
            "# 交通\n" + "".join(str(i) * 49 + "。" for i in range(7)),
            "## 餐饮\n模拟早茶订单 CHUNK1011。兑换时间为上午九点，地点为蓝鲸茶室。",
            "### 长段\n" + "甲" * 700,
        ]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chunking-live", timeout=180
        ) as client:
            response = await client.post(
                "/api/v1/my-knowledge",
                headers=headers,
                json={"title": title, "content": "\n".join(sections)},
            )
            response.raise_for_status()
            assert response.json()["code"] == 0
            async with AsyncSessionLocal() as db:
                rows = (
                    await db.scalars(
                        select(UserKnowledge)
                        .where(UserKnowledge.user_id == user_id)
                        .order_by(UserKnowledge.chunk_index)
                    )
                ).all()
                chunks = [row.content for row in rows]
                assert response.json()["data"]["chunks"] == len(chunks)
                assert all(0 < len(chunk) <= 300 for chunk in chunks)
                assert all(any(chunk in section for section in sections) for chunk in chunks)
                assert [r.chunk_index for r in rows] == list(range(len(rows)))
                assert all(len(row.embedding) == embedding_client.dim for row in rows)
            report["uploaded_chunks"] = [
                {"index": i, "chars": len(c), "content": c} for i, c in enumerate(chunks)
            ]
            response = await client.post(
                "/api/v1/my-knowledge/search",
                headers=headers,
                json={"query": "CHUNK1011 早茶订单在哪里兑换，几点？", "top_k": 3},
            )
            response.raise_for_status()
            assert response.json()["code"] == 0
            hits = response.json()["data"]["items"]
            assert hits and hits[0]["content"] == sections[1]
            assert all(hit["rerank_score"] is not None for hit in hits)
            report["search"] = hits
            response = await client.delete(f"/api/v1/my-knowledge/{title}", headers=headers)
            response.raise_for_status()
            assert response.json()["code"] == 0 and response.json()["data"]["deleted"] == len(
                chunks
            )
            report["deleted_chunks"] = len(chunks)
        assert observer.count == 0, "模型发生回退，不能标记真实模型验收通过"
        report["fallback_warnings"] = observer.count
        report["passed"] = True
    finally:
        try:
            if user_id is not None:
                async with AsyncSessionLocal() as db:
                    await db.execute(delete(User).where(User.id == user_id))
                    await db.commit()
            report["cleaned_up"] = True
        finally:
            for logger in loggers:
                logger.removeHandler(observer)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
    print(f"chunking live: {len(chunks)} chunks, real models passed, cleanup complete", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(verify(parser.parse_args().output.resolve()))
