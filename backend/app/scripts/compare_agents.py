"""单 Agent vs 多 Agent 量化对比（Day 41）。

用法（在 backend 目录下）：

    .venv\\Scripts\\python.exe -m app.scripts.compare_agents

同一批问题分别跑两种架构，输出耗时 / LLM 调用次数 / token 用量的对比表。

**为什么是脚本而不是测试**：这里要调真实模型——慢、花钱、输出每次不同，
放进 CI 只会变成"红线常客"。但架构决策必须有数据支撑，
所以做成"想看就跑一次"的工具。

计数用 LangChain 的 callback（对两种架构一视同仁），
而不是各自内部埋点——否则两边统计口径不同，比出来的数是假的。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.callbacks.usage import UsageMetadataCallbackHandler

from app.core.config import settings
from app.services import agent, multi_agent
from app.services.agent_registry import route_domains
from app.services.user_context import reset_current_user_id, set_current_user_id

# 覆盖各类路由情形的代表性问题
QUESTIONS: list[tuple[str, str]] = [
    ("明天天气怎么样", "单域-天气"),
    ("广州有什么好吃的", "单域-知识"),
    ("明天爬山穿什么", "双域-穿搭依赖天气"),
    ("明天去广州塔穿什么、怎么走", "三域复合"),
    ("你好呀", "闲聊"),
]


class CountCallback(BaseCallbackHandler):
    """统计 LLM 与工具调用次数（token 另由 UsageMetadataCallbackHandler 统计）。"""

    def __init__(self) -> None:
        self.llm_calls = 0
        self.tool_calls = 0
        self.tool_trace: list[dict] = []
        self._runs: dict[str, dict] = {}

    def on_chat_model_start(self, *args: Any, **kwargs: Any) -> None:
        self.llm_calls += 1

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.llm_calls += 1

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs: Any) -> None:
        self.tool_calls += 1
        entry = {
            "name": serialized.get("name", ""),
            "input": input_str,
            "node": (kwargs.get("metadata") or {}).get("langgraph_node"),
            "output": None,
            "error": None,
        }
        self.tool_trace.append(entry)
        self._runs[str(run_id)] = entry

    def on_tool_end(self, output, *, run_id, **kwargs: Any) -> None:
        entry = self._runs.get(str(run_id))
        if entry is not None:
            content = getattr(output, "content", output)
            entry["output"] = str(content)[:24000]

    def on_tool_error(self, error, *, run_id, **kwargs: Any) -> None:
        entry = self._runs.get(str(run_id))
        if entry is not None:
            entry["error"] = type(error).__name__


async def _run_single(
    question: str, *, history=None, memory_context="", user_id=None
) -> dict[str, Any]:
    """跑单 Agent（直接调图，便于挂 callback）。"""
    counter = CountCallback()
    usage = UsageMetadataCallbackHandler()
    graph = await agent._get_agent()
    initial = agent._build_initial_state(question, history, memory_context)
    started = time.perf_counter()
    token = set_current_user_id(user_id)
    try:
        result = await graph.ainvoke(initial, config={"callbacks": [counter, usage]})
    finally:
        reset_current_user_id(token)
    return _stat(result.get("answer", ""), counter, usage, time.perf_counter() - started)


async def _run_multi(
    question: str, *, history=None, memory_context="", user_id=None
) -> dict[str, Any]:
    """跑多 Agent。"""
    counter = CountCallback()
    usage = UsageMetadataCallbackHandler()
    graph = await multi_agent._get_graph()
    initial = {
        "question": question,
        "domains": None,
        "history": history or [],
        "memory_context": memory_context,
        "domain_answers": {},
        "metrics": {},
        "answer": "",
        "error": "",
    }
    started = time.perf_counter()
    token = set_current_user_id(user_id)
    try:
        result = await graph.ainvoke(initial, config={"callbacks": [counter, usage]})
    finally:
        reset_current_user_id(token)
    answer = result.get("answer") or multi_agent.fallback_merge(result.get("domain_answers") or {})
    return {
        **_stat(answer, counter, usage, time.perf_counter() - started),
        "domains": result.get("domains", []),
        "domain_answers": result.get("domain_answers", {}),
    }


def _stat(answer: str, counter: CountCallback, usage: Any, seconds: float) -> dict[str, Any]:
    totals = usage.usage_metadata or {}
    prompt = sum(int(item.get("input_tokens", 0) or 0) for item in totals.values())
    completion = sum(int(item.get("output_tokens", 0) or 0) for item in totals.values())
    return {
        "ms": round(seconds * 1000),
        "llm": counter.llm_calls,
        "tools": counter.tool_calls,
        "tokens": prompt + completion,
        "len": len(answer or ""),
        "answer": answer or "",
        "tool_trace": counter.tool_trace,
    }


def _check_llm_configured() -> bool:
    """没配 Key 就别白跑一轮（否则只会在每个问题上抛鉴权错误）。"""
    key_field = {
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "QWEN_API_KEY",
        "zhipu": "ZHIPU_API_KEY",
        "openai": "OPENAI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
    }.get((settings.LLM_PROVIDER or "").lower())
    if key_field is None:
        return True  # 本地模型（ollama）不需要 Key
    return bool(getattr(settings, key_field, ""))


async def main() -> int:
    if not _check_llm_configured():
        print(f"未配置 {settings.LLM_PROVIDER} 的 API Key，无法对比。")
        return 1

    print(f"模型提供商：{settings.LLM_PROVIDER}")
    header = (
        f"{'问题':<26}{'路由':<10}{'单Agent ms/LLM/tok':<24}{'多Agent ms/LLM/tok':<24}{'耗时比'}"
    )
    print(header)
    print("-" * len(header))

    single_total = multi_total = 0
    for question, kind in QUESTIONS:
        domains = route_domains(question)
        one = await _run_single(question)
        many = await _run_multi(question)
        single_total += one["ms"]
        multi_total += many["ms"]

        ratio = f"{many['ms'] / one['ms']:.2f}x" if one["ms"] else "-"
        single_col = f"{one['ms']}/{one['llm']}/{one['tokens']}"
        multi_col = f"{many['ms']}/{many['llm']}/{many['tokens']}"
        print(
            f"{question:<24}"
            f"{(','.join(domains) or '通用'):<14}"
            f"{single_col:<22}"
            f"{multi_col:<22}"
            f"{ratio:<8}{kind}"
        )

    print("-" * len(header))
    if single_total:
        print(
            f"总耗时：单 Agent {single_total} ms / 多 Agent {multi_total} ms "
            f"（{multi_total / single_total:.2f}x）"
        )
    print("\n解读提示：耗时与 token 之外，还需核对回答覆盖了哪些问题、信息是否有工具依据。")
    print("多 Agent 与单 Agent 使用相同的数据源；更长的回答不代表获得了独有信息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
