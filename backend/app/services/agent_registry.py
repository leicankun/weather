"""领域 Agent 注册表（Day 41）。

多智能体协作的第一步不是画流程图，而是**把工具按领域分好组**。

单 Agent 的问题是「所有工具都交给同一个模型挑」：候选有 10 个，
描述全塞进提示词，模型每次都要在"该用哪个"上做一次判断——
歧义大（`search_my_plans` 与 `check_itinerary_weather` 就极易混），
token 也贵。拆成领域后每个 Agent 只带自己那 1~3 个工具：

| 好处 | 说明 |
| --- | --- |
| 选对工具的概率上升 | 候选从 10 个降到 1~3 个 |
| token 成本下降 | 工具描述不便宜，每域只带自己那份 |
| 提示词能写得更具体 | 天气 Agent 只管天气，不必兼顾穿搭与路线 |

⚠️ **工具名必须与 MCP server / local_tools 里注册的名字逐字一致**：
写错不会报任何错，只会让某个 Agent 手里没有工具、于是凭记忆瞎答。
所以这里配了一组「注册表 ↔ 真实工具」的一致性测试守着。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    """一个领域 Agent 的定义。"""

    key: str
    label: str
    tools: tuple[str, ...]
    prompt: str = ""


# ---------------------------------------------------------------------------
# 领域定义
#
# 这里只管理职责与工具。语义路由示例位于 intent_examples.py。
# ---------------------------------------------------------------------------
DOMAINS: dict[str, Domain] = {
    "weather": Domain(
        key="weather",
        label="天气",
        tools=("get_weather", "get_forecast"),
        prompt=(
            "你是天气查询助手。用 get_weather 查实时天气、get_forecast 查未来预报，"
            "只回答天气本身（温度、降水、风力、湿度），不要延伸到穿搭或路线——"
            "那些有专门的助手负责。数据必须来自工具，禁止编造。"
        ),
    ),
    "outfit": Domain(
        key="outfit",
        label="穿搭",
        tools=("recommend_outfit",),
        prompt=(
            "你是穿搭建议助手。用 recommend_outfit 获取建议"
            "（把用户提到的场景如爬山/逛街、偏好如怕冷，作为参数传入）。"
            "回答要具体到单品与厚度，不要泛泛而谈。"
        ),
    ),
    "route": Domain(
        key="route",
        label="路线",
        tools=("plan_travel_route",),
        prompt=(
            "你是出行路线助手。用 plan_travel_route 查询从出发地到目的地的方案"
            "（含时间/费用/天气评分）。若用户没说出发地，先用默认出发地给方案，"
            "并顺便问一句实际出发地。"
        ),
    ),
    "itinerary": Domain(
        key="itinerary",
        label="行程",
        tools=("check_itinerary_weather", "plan_trip", "search_my_plans"),
        prompt=(
            "你是行程助手，负责三件事：\n"
            "1. 用户问自己已有的行程安排、或行程当天要注意什么 → check_itinerary_weather\n"
            '2. 用户要新排一份行程（如"周末想去广州玩两天"）→ plan_trip\n'
            "3. 用户问自己上传的攻略/笔记内容 → search_my_plans\n"
            "注意 1 与 3 的区别：行程表是结构化的（日期/地点/活动），"
            "私有知识库是文档，别用错。"
        ),
    ),
    "knowledge": Domain(
        key="knowledge",
        label="攻略资讯",
        tools=("search_knowledge", "search_news", "web_search"),
        prompt=(
            "你是本地攻略与资讯助手：本地攻略用 search_knowledge，"
            "站内资讯用 search_news，实时性信息（景区是否开放、最新活动、票价）"
            "用 web_search。只讲检索到的内容，查不到就说查不到。"
        ),
    ),
}

# 领域路由的稳定顺序（结果按此排序，保证同一问题每次路由结果一致）
DOMAIN_ORDER: tuple[str, ...] = tuple(DOMAINS)

# 领域依赖：命中左边时，右边也要一起跑。
#
# 目前只有一条：**穿搭依赖天气**。用户问"明天爬山穿什么"时，
# 他想要的不只是"建议穿冲锋衣"，还有"明天 18℃ 有雨"这个前提——
# 少了它，穿搭建议就是悬空的。实测单 Agent 的提示词里也是这么要求的
# （要求同时调天气与穿搭），多 Agent 拆分后这条约束必须显式补回来，
# 否则会静默地比原来答得少。
#
# 反过来刻意**不加**：
# - route → weather：路线评分内部已经含天气，再加一次是重复调用
# - itinerary → weather：check_itinerary_weather 本身就会取当天天气
DOMAIN_DEPENDS: dict[str, tuple[str, ...]] = {
    "outfit": ("weather",),
}


def tools_for_domain(key: str) -> tuple[str, ...]:
    """该领域的工具名（未知领域返回空元组）。"""
    domain = DOMAINS.get(key)
    return domain.tools if domain else ()


def group_tools(tools: list) -> dict[str, list]:
    """把工具对象按领域分组。

    未在任何领域出现的工具放进 `_unassigned`——**不静默丢弃**：
    将来加了工具却忘了登记，应该能从返回值里看出来（有测试守着）。
    """
    by_name = {getattr(tool, "name", ""): tool for tool in tools}
    grouped: dict[str, list] = {}
    assigned: set[str] = set()

    for key, domain in DOMAINS.items():
        picked = [by_name[name] for name in domain.tools if name in by_name]
        grouped[key] = picked
        assigned.update(tool.name for tool in picked)

    grouped["_unassigned"] = [tool for name, tool in by_name.items() if name not in assigned]
    return grouped
