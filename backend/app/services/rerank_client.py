"""百炼 gte-rerank-v2：重排 RRF 候选，异常时保留原顺序和检索分数。"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class RerankClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key
        if self.api_key is None:
            self.api_key = settings.DASHSCOPE_API_KEY or settings.QWEN_API_KEY
        self.base_url = (base_url or settings.RERANK_BASE_URL).rstrip("/")
        self.model = model or settings.RERANK_MODEL
        self.timeout = settings.RERANK_TIMEOUT if timeout is None else timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key.strip()) and not self.api_key.startswith("sk-xxxxxxxx")

    async def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """完整评分后按分数排序，输入 index 映射回本地资料，不接受服务端文档替换。"""
        fallback = [{**item, "rerank_score": None, "rerank_rank": None} for item in candidates]
        if not candidates or not settings.RERANK_ENABLED:
            return fallback
        if not self.available:
            logger.warning("百炼排序 Key 未配置，保留 RRF 排名")
            return fallback

        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": [f"{item['title']}\n{item['content']}" for item in candidates],
            },
            "parameters": {"top_n": len(candidates), "return_documents": False},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/services/rerank/text-rerank/text-rerank",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            results = data["output"]["results"]
            if not isinstance(results, list) or len(results) != len(candidates):
                raise ValueError("排序结果不完整")
            scores: dict[int, float] = {}
            for result in results:
                index = result["index"]
                score = result["relevance_score"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(candidates)
                    or index in scores
                    or type(score) not in (int, float)
                    or not 0 <= score <= 1
                    or not math.isfinite(score)
                ):
                    raise ValueError("排序结果索引或分数不合法")
                scores[index] = float(score)
            # 同分保留 RRF 顺序；使用未四舍五入的模型分数排序。
            indices = sorted(scores, key=lambda index: (-scores[index], index))
            return [
                {**candidates[index], "rerank_score": scores[index], "rerank_rank": rank}
                for rank, index in enumerate(indices, start=1)
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # 不记录异常正文、密钥或候选内容，避免服务端错误响应泄露私人资料。
            reason = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else type(exc).__name__
            )
            logger.warning("百炼重排序失败（%s），保留 RRF 排名", reason)
            return fallback


rerank_client = RerankClient()
