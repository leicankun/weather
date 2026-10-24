# 测试

向量回忆触发：`python -m pytest tests/unit/test_recall_gate.py tests/unit/test_agent_intent.py tests/unit/test_multi_agent.py --no-db -o addopts= -q`；真实 Embedding/LLM + 合成记忆仓库的普通/SSE 验收：`python -m app.scripts.evaluate_recall_gate --suite acceptance --http --output ../docs/RECALL_GATE_ACCEPTANCE_<新名称>.json`。覆盖无旧关键词也回忆、含旧词仍跳过、上下文继承与隔离、故障/空结果区分、纯历史通用回答和保存状态恢复。数据与范围见 [MEMORY.md](MEMORY.md)。

向量意图识别：`python -m pytest tests/unit/test_agent_intent.py tests/unit/test_agent_registry.py tests/unit/test_embedding_strict.py tests/unit/test_multi_agent.py --no-db -o addopts= -q`。覆盖多标签、阈值分差、上下文与话题切换、用户/会话隔离、审批独占、预热并发/失败重建、普通/SSE 一次路由、检查点保存/恢复与新轮重置，以及严格 Embedding 响应校验。真实模型评测：`python -m app.scripts.evaluate_intent_routing --suite acceptance --http --output ../docs/INTENT_ROUTING_ACCEPTANCE_<新名称>.json`；付费模型、匿名真实接口、不写库、不覆盖旧报告。算法与限制见 [INTENT_ROUTING.md](INTENT_ROUTING.md)。

单模式工具预算：`python -m pytest tests/unit/test_agent_tool_budget.py --no-db -o addopts= -q`；PostgreSQL 恢复回归 `tests/api/test_agent_tool_budget_recovery.py`。故意无限调用的模型替身通过真实图及普通/SSE 接口，验证逐工具项计数、批次超限不执行、错误调用消耗额度、正常收尾、会话隔离、检查点恢复与新一轮重置。计数边界见 [AGENT_TOOL_BUDGET.md](AGENT_TOOL_BUDGET.md)。

日期约束回归：`python -m pytest tests/unit/test_temporal_context.py tests/unit/test_temporal_agent_graph.py --no-db -o addopts= -q`，覆盖冻结日期、改期/否定、按任务绑定多个日期、精确选择天气、缺失不回退、错误参数在 IO 前拦截、普通/SSE 草稿隔离及核验误放行的确定性日期拒绝。真实 PostgreSQL 补充 `tests/api/test_temporal_history.py` 与 `tests/api/test_checkpoints.py`，验证原消息日期基准、跨日恢复和工具不重复执行。流程见 [AGENT_DATES.md](AGENT_DATES.md)。

RAG 工具完整证据回归：`python -m pytest tests/unit/test_rag_tool_context.py tests/unit/test_multi_agent.py tests/unit/test_answer_verification.py --no-db -o addopts= -q`。覆盖公共/私人实际工具经过单/多图后保留块尾事实、来源与换行、核验完整证据、整体预算省略和 MCP 文本解包。真实图与聊天 HTTP 验收使用 `app.scripts.evaluate_rag_errors`，会调用已配置真实模型、创建并清理合成资料；命令与结果见 [RAG_TOOL_CONTEXT.md](RAG_TOOL_CONTEXT.md)。

Agent 人工审批回归：`python -m pytest tests/api/test_agent_approvals.py tests/api/test_checkpoints.py -o addopts= -q`。使用真实 PostgreSQL、官方 saver、LangGraph interrupt 和单/多图；模型提案替身覆盖普通/SSE 暂停、连接释放、未批准不执行、批准/拒绝、参数篡改和跨用户隔离、审批过期/范围变化、清空水位、并发决定、批量事务回滚及执行回执幂等。前端 `npm run test:e2e` 另覆盖审批卡片、刷新找回和严格决定请求，契约 API 使用 mock。真实模型验收：`python -m app.scripts.verify_agent_approval --output ../docs/AGENT_APPROVAL_LIVE_<新名称>.json`，调用配置的付费模型与开发 PostgreSQL，通过 FastAPI ASGI 创建、审批并清理合成账号；已有报告拒绝覆盖。流程见 [AGENT_APPROVAL.md](AGENT_APPROVAL.md)，实际结果见 [审批验证记录](AGENT_APPROVAL_VALIDATION_2026-10-11.md)。

Checkpointer 回归：`python -m pytest tests/api/test_checkpoints.py tests/api/test_migrations.py -o addopts= -q`。使用真实 PostgreSQL、官方 saver 与完整单/多 Agent 图，模型替身覆盖节点中断、并行 pending writes、恢复幂等、摘要/卡片/历史 RAG 兼容、用户会话模式隔离、并发与级联删除。真实模型验收：`python -m app.scripts.verify_checkpoint_memory --output ../docs/CHECKPOINT_LIVE_RESULTS_2026-10-11.json`，会调用配置的付费模型，以临时合成账号测试并清理；已有报告拒绝覆盖。架构和恢复 API 见 [CHECKPOINT_MEMORY.md](CHECKPOINT_MEMORY.md)，结果见 [验证记录](CHECKPOINT_VALIDATION_2026-10-11.md)。

前端：`npm --prefix frontend-user run lint`、`npm test`、`npm run build`。根目录 `npm run check:js` 检查两端没有 TS 源文件。

浏览器：`npm run test:e2e`。首次安装 Chromium：在 frontend-user 执行 `npx playwright install chromium`。可在 PowerShell 设置 `$env:PLAYWRIGHT_CHANNEL = 'chrome'` 使用现有 Chrome。

当前浏览器回归覆盖天气字段与移动布局、登录跳转、SSE 对话、草案生成后显式保存、偏好保存回读、错误重试、路线穿搭和各页面启动。API 在测试中按契约拦截，不代替真实数据库/模型验证。截图与 trace 放在 test-results，避免加入 Git。

后端：backend 目录执行 `python -m pytest`；完整套件依赖 PostgreSQL（vector / PostGIS）。测试库独立命名为 weather_db_test，会重建表。

`tests/api/test_migrations.py` 额外创建名称为 `weather_migration_<随机值>_test` 的临时数据库，用 Alembic 从空库迁移到最新版本，验证所有模型表存在及个人知识库接口的增删查、向量检索和用户隔离。另一个用例验证从旧版本升级时保留 ORM 已建表中的数据。测试结束删除临时库；测试账号需有创建数据库权限，迁移还需要 TimescaleDB 扩展。该回归不依赖 `create_all` 初始化，能发现迁移漏建表的问题。

该迁移回归还验证公共知识库的 `(source, chunk_index)` 唯一约束、重复 upsert 更新正文与元数据、两个独立会话并发 upsert 只留下一个分块，以及旧库已有 ORM 同名约束时正常升级。

无数据库的本地验证使用 `python -m pytest --no-db -o addopts= -q`。该模式显式跳过依赖 db/client 等集成夹具的用例，并禁止初始化/清理数据库。默认模式继续运行完整套件，CI 仍使用真实 PostgreSQL。

每次验证的实际通过数、跳过数、环境限制记录在 VALIDATION.md。不能把 no-db 或前端 mock 测试表述成全栈联调成功。

文档分块回归：`python -m pytest tests/unit/test_knowledge_chunking.py tests/api/test_chunking_knowledge_api.py -o addopts= -q`，覆盖标题与重叠隔离、代码围栏、段／句回退、超长硬切、字符覆盖及真实数据库公共/个人入库。纯切分可加 `--no-db` 只运行单元文件。真实模型/API 验收：`python -m app.scripts.verify_chunking --output ../docs/CHUNKING_LIVE_2026-10-11_FINAL.json`，调用配置的付费 Embedding/rerank API，以临时账号上传、检索、删除并清理账号；已有报告拒绝覆盖，复测需选择新路径。结果见 [CHUNKING_VALIDATION_2026-10-11.md](CHUNKING_VALIDATION_2026-10-11.md)。

LangChain 检索编排回归：`python -m pytest tests/unit/test_langchain_retrieval.py tests/api/test_langchain_knowledge.py tests/unit/test_hybrid_retrieval.py tests/unit/test_rerank_client.py tests/api/test_hybrid_knowledge.py tests/api/test_rerank_knowledge.py tests/api/test_memory.py -o addopts= -q`。验证原生 Ensemble RRF、并行召回、callbacks、Document 元数据、标准资料问答链组合，以及现有接口/历史/用户隔离和故障降级。真实模型/API 验收：`python -m app.scripts.verify_langchain_rag --output ../docs/LANGCHAIN_RAG_LIVE_2026-10-11.json`，创建并清理临时合成账号，调用已配置付费 API，已有报告拒绝覆盖。实际结果见 [LANGCHAIN_RAG_VALIDATION_2026-10-11.md](LANGCHAIN_RAG_VALIDATION_2026-10-11.md)。

短期会话摘要回归：`python -m pytest tests/api/test_chat_summary.py tests/unit/test_chat_summary_context.py tests/api/test_chat_history.py tests/api/test_memory.py tests/api/test_migrations.py tests/unit/test_answer_verification.py -o addopts= -q`。真实 PostgreSQL 和模型替身验证每五轮检查、超字符预算才压缩、保留最近五轮、过渡消息不丢失、摘要更新与删除并发、模型故障/超时、普通/SSE 单/多模式及原始聊天保留。测试默认关闭摘要外部调用，新用例显式开启。真实模型/API 验收使用 `python -m app.scripts.verify_chat_summary`；临时合成账号、开发库和已配置付费模型，结束清理，不修改 `.env`，已有报告拒绝覆盖。设计与配置见 [CHAT_SUMMARY.md](CHAT_SUMMARY.md)。

多智能体回答核验：`python -m pytest tests/unit/test_answer_verification.py tests/unit/test_multi_agent.py tests/unit/test_compare_agents.py --no-db -o addopts= -q`，覆盖独立核验、工具证据交接、修正复查、异常不发布及 SSE 草稿隔离。真实模型与普通/SSE 接口验收：`python -m app.scripts.verify_multi_agent`，会调用付费 API；已有输出拒绝覆盖，可传 `--output` 指定新路径。测试方法与模型漏检边界见 [核验文档](AGENT_VERIFICATION.md) 和 [实际验收](AGENT_VERIFICATION_LIVE_2026-10-10.md)。

单 / 多 Agent 对照入口回归：`python -m pytest tests/unit/test_compare_agents.py tests/unit/test_multi_agent.py tests/unit/test_agent_intent.py --no-db -o addopts= -q`，验证初始路由、相同背景与用户身份、真实图执行及工具追踪。真实模型对照使用 `python -m app.scripts.compare_agents_live`：12 个问题加 3 次重复、30 份回答，使用开发库临时合成账号和真实付费 API，结束清理账号；不修改 `.env` 或保存模型回答到聊天历史。报告默认覆盖 `docs/AGENT_COMPARISON_<日期>.json`；人工评估见 [2026-10-10 对照报告](AGENT_COMPARISON_2026-10-10.md)，不得把回退路线估算或无依据的新增内容算作多 Agent 优势。

补齐高德 / Tavily 后的相同问题复测见 [WITH_KEYS 报告](AGENT_COMPARISON_2026-10-10_WITH_KEYS.md)。原基线保留；额外验证 API 业务成功及搜索来源、部分路线缺失、费用 / 日期语义，并保留脱敏服务观察。对照入口、意图、多智能体和高德 / 搜索相关回归：`python -m pytest tests/unit/test_compare_agents.py tests/unit/test_multi_agent.py tests/unit/test_agent_intent.py tests/unit/test_amap_client.py tests/unit/test_web_search_service.py --no-db -o addopts= -q`。

双层记忆回归：`python -m pytest tests/api/test_memory.py tests/api/test_chat_history.py tests/api/test_migrations.py -o addopts= -q`。使用真实 PostgreSQL 和单/多智能体图，模型替身覆盖普通/SSE 跨会话卡片、超过 10 条的历史回忆、证据/角色/用户隔离、真实两路 SQL 与 rerank HTTP 契约、并发更新/回填、清空期间在途写入、删除来源级联、预算与故障降级。全局默认关闭自动记忆外部调用，这些用例显式开启并替换模型。

真实模型验收：backend 中 `python -m app.scripts.verify_memory`，使用开发库、真实对话/Embedding/rerank API 和临时合成账号；会产生 API 调用费用，不作为 CI 用例。覆盖新事实、偏好更正、跨会话回忆、假设过滤、单/多模式、用户隔离和清空后的回填，输出到 `docs/MEMORY_LIVE_RESULTS_2026-10-10.json`，结束时清理临时账号。

多智能体多轮对话回归：`python -m pytest tests/api/test_chat_history.py tests/unit/test_multi_agent.py tests/unit/test_chat_history_service.py -o addopts= -q`。API 用例使用真实 PostgreSQL 和完整 LangGraph 图，模型与外部工具使用替身，覆盖普通及 SSE 两轮对话、领域与汇总节点接收历史、通用回答、最近 10 条限制、用户/会话隔离和匿名请求。单元用例另覆盖省略问题的上下文路由及工具循环不改写共享历史。

混合检索回归：`python -m pytest tests/unit/test_hybrid_retrieval.py tests/api/test_hybrid_knowledge.py tests/api/test_migrations.py -o addopts= -q`。包含 BM25 数学性质、中文及编号分词、RRF 融合和去重、两路互补、无向量召回、用户/公共私有隔离、删除后结果变化及 API 返回分数字段；数据库查询真实执行，Embedding API 使用替身。详细链路见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

重排序回归：`python -m pytest tests/unit/test_rerank_client.py tests/api/test_rerank_knowledge.py -o addopts= -q`。真实数据库和 API 链路接入百炼 HTTP 替身，覆盖请求格式、输入索引映射、最终 Top-K 之前重排、私有候选隔离、关闭/无 Key、超时、401/429/503、无效响应和异常分数降级。测试全局默认关闭外部重排，新用例显式开启并通过 `respx` 拦截，开发配置不触发真实模型调用。模型能力和账号可用性仍须通过配置真实 Key 的接口联调确认。
