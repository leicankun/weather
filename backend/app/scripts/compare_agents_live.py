"""真实模型、真实工具、合成私有资料的单/多 Agent 对照实验。

backend 中运行 python -m app.scripts.compare_agents_live；会调用付费 API。
不修改 .env，不写入两种模式的回答到对话历史；临时账号结束后删除。
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.itinerary import Itinerary
from app.models.user import User
from app.schemas.memory import CardPatch
from app.scripts.compare_agents import _run_multi, _run_single
from app.services import chat_history_service, memory_service, user_knowledge_service
from app.services.agent_registry import DOMAINS
from app.services.llm_client import get_llm

CASES = [
    ("weather", "广州今天气温和降雨情况怎么样？"),
    ("outfit", "广州今天上班怎么穿？我怕冷。"),
    ("route", "从广州南站到广州塔怎么走？比较地铁和打车的时间、费用。"),
    ("knowledge", "广州有哪些适合雨天的室内景点？推荐两个并说明理由。"),
    ("three_domains", "广州今天气温、下雨情况怎么样？去广州塔穿什么？从广州南站坐地铁怎么走？"),
    (
        "four_domains",
        "明天从广州南站出发去广州塔，查天气、推荐穿搭、比较地铁和打车路线，再推荐一个附近适合雨天的室内景点。分别回答。",
    ),
    ("itinerary", "查询我明天的行程安排，说明活动地点和开始时间，再结合天气提醒我。"),
    ("private", "我上传的星河书店攻略里，集合时间、集合点和入场口令分别是什么？"),
    (
        "private_weather_outfit",
        "按我上传的星河书店攻略，复述集合时间、集合点和口令；再查广州今天的天气，给穿搭建议。",
    ),
    ("memory", "我以前说过常住哪里、吃什么口味，活动暗号是什么？"),
    ("follow_up", "那我今天去那里怎么穿？"),
    (
        "itinerary_route_outfit",
        "我明天上午的行程几点、在哪里？下午从广州南站去广州塔怎么走？根据广州明天天气给穿搭建议。",
    ),
]
MARKERS = {
    "private": ["13:40", "鹭江", "青禾714"],
    "private_weather_outfit": ["13:40", "鹭江", "青禾714"],
    "memory": ["海珠", "微辣", "蓝鲸7429"],
    "itinerary": ["09:20", "广州博物馆"],
    "itinerary_route_outfit": ["09:20", "广州博物馆"],
}


async def main():
    output = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / f"AGENT_COMPARISON_{date.today().isoformat()}.json"
    )
    report = {
        "at": datetime.now(UTC).isoformat(),
        "provider": settings.LLM_PROVIDER,
        "model": get_llm().model_name,
        "temperature": get_llm().temperature,
        "weather_provider": settings.WEATHER_PROVIDER,
        "amap_configured": bool(settings.AMAP_API_KEY),
        "web_search_configured": bool(settings.TAVILY_API_KEY),
        "pairs": [],
        "temporary_data_removed": False,
    }
    user_id = None
    try:
        async with AsyncSessionLocal() as db:
            user = User(username=f"compare_live_{uuid4().hex[:10]}", password_hash="unused")
            db.add(user)
            await db.flush()
            user_id = user.id
            db.add(
                Itinerary(
                    user_id=user_id,
                    title="合成测试：广州博物馆参观",
                    date=(date.today() + timedelta(days=1)).isoformat(),
                    start_time="09:20",
                    location="广州博物馆",
                    activity="室内参观",
                )
            )
            await db.commit()
        await user_knowledge_service.add_document(
            user_id,
            "星河书店攻略（合成测试资料）",
            "这是一份合成测试攻略，不代表真实活动。星河书店活动集合时间为13:40，集合点为鹭江地铁站A出口，入场口令为青禾714。活动只安排阅读和室内讨论。",
        )
        source = await chat_history_service.add_message(
            user_id, "profile", "user", "我长期住在海珠区，现在喜欢微辣。"
        )
        await memory_service.apply_cards(
            user_id,
            source["id"],
            [
                CardPatch(
                    kind="fact", key="residence", value="长期住在海珠区", evidence="长期住在海珠区"
                ),
                CardPatch(kind="preference", key="diet", value="喜欢微辣", evidence="喜欢微辣"),
            ],
        )
        await chat_history_service.add_message(
            user_id, "old-event", "user", "上次活动的暗号是蓝鲸7429。"
        )
        await memory_service.index_history(user_id)
        from app.services import agent, multi_agent

        all_tools = await agent._get_all_tools()
        report["single_tool_names"] = [tool.name for tool in all_tools]
        report["multi_tool_names"] = sorted(
            {name for domain in DOMAINS.values() for name in domain.tools}
        )
        await agent._get_agent()
        await multi_agent.load_domain_tools()
        await multi_agent._get_graph()
        selected = [(key, question, 1) for key, question in CASES]
        selected.extend(
            (key, question, 2)
            for key, question in CASES
            if key in {"three_domains", "private_weather_outfit", "itinerary_route_outfit"}
        )
        for index, (key, question, repeat) in enumerate(selected):
            history = []
            if key == "follow_up":
                history = [
                    {"role": "user", "content": "我今天准备从广州南站去广州塔。"},
                    {"role": "assistant", "content": "目的地是广州塔，可以比较地铁和打车。"},
                ]
            memory = await memory_service.prepare_context(user_id, f"compare-{key}", question)
            pair = {
                "id": key,
                "question": question,
                "repeat": repeat,
                "history": history,
                "memory_context": memory,
                "run_order": ["single", "multi"] if index % 2 == 0 else ["multi", "single"],
            }
            for mode in pair["run_order"]:
                try:
                    async with asyncio.timeout(150):
                        runner = _run_single if mode == "single" else _run_multi
                        result = await runner(
                            question, history=history, memory_context=memory, user_id=user_id
                        )
                        result["known_fact_hits"] = [
                            marker for marker in MARKERS.get(key, []) if marker in result["answer"]
                        ]
                        pair[mode] = result
                except Exception as exc:  # noqa: BLE001 保存失败，继续对照，不把缺数据算成劣势
                    pair[mode] = {"error": type(exc).__name__, "answer": "", "tool_trace": []}
                print(
                    f"pair {index + 1}/{len(selected)} {key} repeat={repeat} {mode} ms={pair[mode].get('ms')} tools={[t['name'] for t in pair[mode]['tool_trace']]}",
                    flush=True,
                )
            report["pairs"].append(pair)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if user_id is not None:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(User).where(User.id == user_id))
                await db.commit()
                report["temporary_data_removed"] = await db.get(User, user_id) is None
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
