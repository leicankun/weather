"""用示例向量相似度决定是否检索历史；不使用回忆关键词或对话模型分类。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field

from app.core.config import settings
from app.services.embedding_client import EmbeddingClient
from app.services.semantic_examples import ExampleVectorIndex

logger = logging.getLogger(__name__)

RECALL_EXAMPLES: dict[str, tuple[str, ...]] = {
    "history": (
        "我上次说过的餐厅叫什么？",
        "我以前告诉过你哪些饮食限制？",
        "帮我找回聊过的那份旅行计划",
        "之前活动的暗号是什么？",
        "我常住在哪里？",
        "我喜欢吃什么口味？",
        "那家餐厅叫什么？",
        "我给你说的集合时间是几点？",
        "我们聊过的目的地是哪儿？",
        "我提到的那个人叫什么名字？",
        "帮我回顾一下我们讨论的事情",
        "我保存的过敏信息有哪些？",
        "我原来决定去哪家店来着？",
        "之前说过什么？",
        "之前的暗号是什么？",
        "之前的饮食限制是什么？",
        "我之前给你的预算上限是多少？",
        "我的旅游预算是多少？",
    ),
    "mixed": (
        "根据我平时的口味推荐晚餐",
        "沿用我原来的计划，重新安排明天的游玩路线",
        "查一下我们聊过的那个目的地明天的天气",
        "按照我以前的穿衣习惯推荐衣服",
        "结合我过去说的预算安排这次旅行",
        "参考我聊过的餐馆，再推荐几家类似的",
        "按我的过敏情况推荐能吃的食物",
        "用我之前提到的出发地规划路线",
    ),
    "current": (
        "广州明天天气怎么样？",
        "从广州南站到广州塔怎么走？",
        "推荐几家广州餐厅",
        "介绍广州的历史文化",
        "历史上广州发生过哪些台风？",
        "以前的广州叫什么名字？",
        "帮我解释什么是历史记录",
        "之前那个台风是哪年登陆的？",
        "请记得提醒游客带伞",
        "你好呀",
        "谢谢你",
        "你是谁？",
        "讲一个故事",
        "明天穿什么衣服？",
        "周末玩两天怎么安排？",
        "我今天不想吃辣，推荐一家餐厅",
        "不用参考我的历史，直接介绍景点",
        "不用回忆，直接查广州天气",
        "我长期住在天河区，我不吃辣。",
        "清空我的长期记忆",
        "删除我以前的聊天记录",
        "请在路线建议里标注路程和耗时",
        "记得在回答中列出交通方式与时间",
    ),
    "followup": (
        "那后来呢？",
        "还有细节吗？",
        "换个日期再查呢？",
        "那后天呢？",
        "继续刚才的问题",
        "再详细说一下",
        "那明天呢？",
        "换成深圳呢？",
    ),
}


@dataclass
class RecallDecision:
    need_recall: bool = False
    query: str = ""
    history_only: bool = False
    status: str = "not_needed"
    scores: dict[str, float] = field(default_factory=dict)

    def state(self) -> dict:
        return asdict(self)


class SemanticRecallGate:
    def __init__(self) -> None:
        self.index = ExampleVectorIndex(RECALL_EXAMPLES)

    def reset(self) -> None:
        self.index.reset()

    async def _decide(self, question: str, history: list[dict]) -> RecallDecision:
        client = EmbeddingClient(timeout=settings.MEMORY_RECALL_TIMEOUT)
        if not client.available:
            raise ValueError("Semantic embedding provider is not configured")

        async def classify(value):
            scores = (await self.index.scores(client, [value[:2000]]))[0]
            best = max(scores, key=scores.get)
            if scores[best] < settings.MEMORY_RECALL_THRESHOLD:
                best = "uncertain"
            return best, {key: round(score, 6) for key, score in scores.items()}

        label, scores = await classify(question)
        if label in {"history", "mixed"}:
            return RecallDecision(True, question[:2000], label == "history", "matched", scores)
        if label != "followup":
            return RecallDecision(
                status="uncertain" if label == "uncertain" else "not_needed", scores=scores
            )
        users = [item for item in history if item.get("role") == "user"]
        previous = users[-settings.MEMORY_RECALL_CONTEXT_TURNS :]
        if not users:
            previous = [item for item in history if item.get("role") == "summary"][-1:]
        for item in reversed(previous):
            text = str(item.get("content", ""))[:1000]
            prior, _ = await classify(text)
            if prior == "followup":
                continue
            if prior in {"history", "mixed"}:
                return RecallDecision(
                    True,
                    f"前文问题：{text}\n本次追问：{question[:1000]}",
                    prior == "history",
                    "inherited",
                    scores,
                )
            break
        return RecallDecision(status="not_needed", scores=scores)

    async def decide(self, question: str, history: list[dict] | None = None) -> RecallDecision:
        if not question.strip():
            return RecallDecision()
        try:
            return await asyncio.wait_for(
                self._decide(question, history or []), settings.MEMORY_RECALL_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 memory enhancement must not block chat
            logger.warning("Semantic recall decision unavailable: %s", type(exc).__name__)
            return RecallDecision(status="unavailable")


recall_gate = SemanticRecallGate()
