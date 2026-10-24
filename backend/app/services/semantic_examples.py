"""共享的严格语义示例索引：只缓存公共示例，不缓存用户问题或历史。"""

from __future__ import annotations

import asyncio
import hashlib
import math
from weakref import WeakKeyDictionary

from app.core.config import settings
from app.services.embedding_client import EmbeddingClient


def normalize(vector: list[float], dim: int) -> tuple[float, ...]:
    if len(vector) != dim or not all(math.isfinite(value) for value in vector):
        raise ValueError("Invalid semantic embedding")
    norm = math.hypot(*vector)
    if not norm or not math.isfinite(norm):
        raise ValueError("Invalid semantic embedding norm")
    return tuple(value / norm for value in vector)


class ExampleVectorIndex:
    def __init__(self, examples: dict[str, tuple[str, ...]]) -> None:
        self.examples = examples
        self._cache: dict = {}
        self._locks: WeakKeyDictionary = WeakKeyDictionary()

    def reset(self) -> None:
        self._cache.clear()
        self._locks.clear()

    async def _vectors(self, client: EmbeddingClient):
        entries = [(label, text) for label, texts in self.examples.items() for text in texts]
        key = (
            client.base_url,
            client.model,
            client.dim,
            hashlib.sha256(client.api_key.encode()).hexdigest(),
            tuple(entries),
        )
        lock = self._locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        async with lock:
            if key not in self._cache:
                vectors = []
                for start in range(0, len(entries), settings.EMBED_BATCH_SIZE):
                    texts = [text for _, text in entries[start : start + settings.EMBED_BATCH_SIZE]]
                    batch = await client.embed_texts(texts, allow_fallback=False)
                    if len(batch) != len(texts):
                        raise ValueError("Incomplete exemplar embeddings")
                    vectors.extend(normalize(vector, client.dim) for vector in batch)
                self._cache = {
                    key: list(zip((label for label, _ in entries), vectors, strict=True))
                }
            return self._cache[key]

    async def scores(self, client: EmbeddingClient, texts: list[str]) -> list[dict[str, float]]:
        examples = await self._vectors(client)
        vectors = await client.embed_texts(texts, allow_fallback=False)
        if len(vectors) != len(texts):
            raise ValueError("Incomplete query embeddings")
        result = []
        for vector in vectors:
            query = normalize(vector, client.dim)
            by_label: dict[str, list[float]] = {}
            for label, example in examples:
                by_label.setdefault(label, []).append(
                    sum(a * b for a, b in zip(query, example, strict=True))
                )
            result.append(
                {
                    label: sum(sorted(values, reverse=True)[:2]) / min(2, len(values))
                    for label, values in by_label.items()
                }
            )
        return result
