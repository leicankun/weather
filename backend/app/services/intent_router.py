"""示例问句向量路由：多领域、按轮继承上下文、明确拒绝低分和服务故障。"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field

from app.core.config import settings
from app.services import approval_service
from app.services.agent_registry import DOMAIN_DEPENDS, DOMAIN_ORDER
from app.services.embedding_client import EmbeddingClient
from app.services.intent_examples import INTENT_EXAMPLES
from app.services.semantic_examples import ExampleVectorIndex

logger = logging.getLogger(__name__)
UNCERTAIN_ANSWER = "还不能确定你想查询什么，请补充具体问题，例如天气、穿搭、路线、行程或攻略。"
UNAVAILABLE_ANSWER = "意图识别服务暂时不可用，请稍后重试。"


@dataclass
class IntentDecision:
    domains: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    status: str = "matched"
    inherited: bool = False

    def state(self) -> dict:
        return asdict(self)

    @property
    def stop_answer(self) -> str:
        return {"uncertain": UNCERTAIN_ANSWER, "unavailable": UNAVAILABLE_ANSWER}.get(
            self.status, ""
        )


def expand_domains(domains: list[str]) -> list[str]:
    picked = set(domains).intersection(DOMAIN_ORDER)
    for key in list(picked):
        picked.update(DOMAIN_DEPENDS.get(key, ()))
    return [key for key in DOMAIN_ORDER if key in picked]


class SemanticIntentRouter:
    def __init__(self) -> None:
        self.index = ExampleVectorIndex(INTENT_EXAMPLES)

    def reset(self) -> None:
        self.index.reset()

    async def _classify(self, question: str, client) -> IntentDecision:
        # Split on punctuation/general conjunctions; no business words select domains.
        clauses = [
            part.strip()
            for part in re.split(r"[，,。；;！？!?、\n]+|以及|并且|同时|还有|和", question)
            if part.strip()
        ]
        clauses = clauses[:8] or [question]
        clause_scores = await self.index.scores(client, clauses)
        all_scores: dict[str, float] = {}
        picked = set()
        statuses = []
        threshold = settings.INTENT_SIMILARITY_THRESHOLD
        margin = settings.INTENT_MULTI_MARGIN
        for scores in clause_scores:
            for label, score in scores.items():
                all_scores[label] = max(all_scores.get(label, -1), score)
            best = max(scores, key=scores.get)
            if scores[best] < threshold:
                statuses.append("uncertain")
            elif best in {"general", "followup", "ignore"}:
                statuses.append(best)
            else:
                statuses.append("matched")
                picked.update(
                    label
                    for label in DOMAIN_ORDER
                    if scores[label] >= threshold and scores[best] - scores[label] <= margin
                )
        status = (
            "matched"
            if picked
            else (
                "followup"
                if "followup" in statuses
                else "uncertain"
                if "uncertain" in statuses
                else "general"
            )
        )
        return IntentDecision(
            expand_domains(list(picked)), {k: round(v, 6) for k, v in all_scores.items()}, status
        )

    async def _route(self, question: str, history: list[dict]) -> IntentDecision:
        client = EmbeddingClient(timeout=settings.INTENT_ROUTING_TIMEOUT)
        if not client.available:
            raise ValueError("Semantic embedding provider is not configured")
        decision = await self._classify(question[:4000], client)
        if decision.status != "followup":
            return decision
        # A new explicit topic wins above. A followup inherits the most recent user topic;
        # assistant prose is never independently treated as the user's intent.
        users = [item for item in history if item.get("role") == "user"]
        previous = users[-settings.INTENT_CONTEXT_TURNS :]
        if not users:
            previous = [item for item in history if item.get("role") == "summary"][-1:]
        for item in reversed(previous):
            text = str(item.get("content", ""))[:2000]
            if not text.strip() or approval_service.is_management_request(text):
                break
            prior = await self._classify(text, client)
            if prior.status == "followup":
                continue
            if prior.domains:
                return IntentDecision(prior.domains, decision.scores, "matched", True)
            break  # Do not resurrect old tasks across a topic change or small talk.
        return IntentDecision(scores=decision.scores, status="uncertain")

    async def route(self, question: str, history: list[dict] | None = None) -> IntentDecision:
        # These are prompt-envelope delimiters, not business intent keywords.
        # Preferences and attachment bodies must not create extra user requests.
        question = re.split(
            r"\n\n【(?:用户偏好】|附件：|语音转写：|系统说明：)", question, maxsplit=1
        )[0]
        # Existing destructive-operation detection stays outside semantic business routing.
        if approval_service.is_management_request(question):
            return IntentDecision(domains=["management"])
        if not question.strip():
            return IntentDecision(status="uncertain")
        try:
            return await asyncio.wait_for(
                self._route(question, history or []), settings.INTENT_ROUTING_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 provider/validation failures must stop routing
            logger.warning("Semantic intent routing unavailable: %s", type(exc).__name__)
            return IntentDecision(status="unavailable")


intent_router = SemanticIntentRouter()


async def identify_intent(
    question: str, history: list[dict] | None = None, *, memory_context: str = ""
) -> IntentDecision:
    from app.services.memory_service import history_only_question

    if history_only_question(memory_context) and not approval_service.is_management_request(
        question
    ):
        return IntentDecision(status="general")
    return await intent_router.route(question, history)


async def routing_result_node(state: dict) -> dict:
    return {"answer": state["routing_stop"]}
