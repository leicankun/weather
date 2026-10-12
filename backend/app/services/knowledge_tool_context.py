"""Bound RAG tool output by complete ranked chunks, never by per-chunk previews."""

from __future__ import annotations

import json
from typing import Any

# Keep the complete serialized tool output within answer_verification's
# per-tool evidence allowance so model and reviewer receive the same content.
MAX_KNOWLEDGE_TOOL_CHARS = 6000


def format_knowledge_context(
    hits: list[dict[str, Any]], *, scope: str, max_chars: int = MAX_KNOWLEDGE_TOOL_CHARS
) -> str:
    """Include a prefix of ranked full chunks and explicitly report omissions."""
    included: list[dict[str, Any]] = []

    def serialize(chunks: list[dict[str, Any]]) -> str:
        omitted = len(hits) - len(chunks)
        return json.dumps(
            {
                "scope": scope,
                "retrieved_count": len(hits),
                "included_count": len(chunks),
                "omitted_count": omitted,
                "context_incomplete": bool(omitted),
                "notice": (
                    "整体上下文预算不足，部分检索分块未纳入。当前片段不足以确认的信息，不能断言原资料没有，也不能猜测上传或索引失败。"
                    if omitted
                    else "已保留本次检索返回分块的完整正文；这些片段不是整个知识库。"
                ),
                "items": chunks,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    if len(serialize([])) > max_chars:
        raise ValueError("知识库工具上下文预算不足以容纳状态说明")
    for hit in hits:
        chunk = {
            "id": hit.get("id"),
            "title": hit.get("title", ""),
            "source": hit.get("source", ""),
            "chunk_index": hit.get("chunk_index"),
            "rerank_score": hit.get("rerank_score"),
            "rrf_score": hit.get("rrf_score"),
            "content": hit.get("content") or "",
        }
        candidate = [*included, chunk]
        if len(serialize(candidate)) > max_chars:
            break
        included = candidate
    return serialize(included)
