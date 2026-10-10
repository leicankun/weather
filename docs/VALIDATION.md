# 重构验证记录

## 2026-10-10：偏好与事实卡片 + 历史 RAG

新增共享记忆管理器、JSONB 卡片、1024 维历史切片和清空水位；复用既有 BM25 + pgvector + RRF + `gte-rerank-v2`。普通/SSE、单/多模式均接入，原始一轮消息原子保存，SSE done 在记忆处理完成或超时后发出。开发库已应用 `0016_chat_memory`；本地 `.env` 和任务前的 Embedding 客户端编辑保留。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 207 个文件通过，仅任务前 `embedding_client.py:81` 行尾空格未通过 |
| 新迁移显式 Ruff 和格式检查 | 通过，默认检查不包含 Alembic 目录 |
| 记忆、聊天历史、混合检索、多智能体及迁移相关回归 | 98 项通过；当时新增记忆用例 16 项 |
| 最终 `tests/api/test_memory.py` | 17 项通过，补充空记忆能力描述回归，含空白消息回填与最终提示词验证 |
| `python -m pytest` | 785 项通过、2 项原有通知用例失败，无跳过；运行于追加空记忆措辞用例前 |
| 实际迁移与运行中 HTTP 服务 | 开发库升级成功，HTTP OpenAPI 挂载 4 个记忆接口；未登录查看卡片返回 401 |
| 真实模型验收 | 首批 12 次 + 修复后 2 次对话；实际 DeepSeek、智谱 Embedding 和百炼 rerank；临时账号已清理 |
| `npm run check:js`、用户端 lint | 通过 |
| `npm test` | 5 个文件、18 项通过 |
| `npm run build` | 两端通过，保留原有大产物提示 |
| `npm run test:e2e`，Chrome | 9 项通过，使用 mock API 的浏览器契约测试 |

完整套件的两个失败仍为 `TestVapidKey::test_返回公钥`（本地 VAPID 公钥为空）和 `TestPrefs::test_默认全开`（预期字段未包含既有 `risk_enabled`），与本次记忆改动无关。最终记忆回归覆盖原话证据校验、角色/用户隔离、慢请求不能覆盖新卡片、并发回填不重复、清空期间在途写入不能复活、来源会话删除级联、字符预算、最近消息去重及外部模型故障降级。

真实模型验证成功回忆超过最近 10 条的活动暗号、跨会话使用卡片、更正居住地和口味、过滤假设并隔离另一用户。未命中时模型最初误称“没有记忆功能”；已修正空背景提示并用两次真实 SSE 补测确认改为“目前没找到相关记录”。详见 [实际模型记录](MEMORY_LIVE_TEST_2026-10-10.md) 和 [实现与边界](MEMORY.md)。旧聊天分批回填、回忆措辞触发检索、卡片主题归并依赖模型；尚无摘要或单独的前端记忆管理页。

验证日期：2026-10-09。Windows / Python 3.13.2 / Node.js 24.16.0。

## 已通过

| 检查 | 结果 |
| --- | --- |
| `npm run check:js` | 两个前端无 TypeScript 源文件或 Vue TS 脚本 |
| `npm --prefix frontend-user run lint` | ESLint 通过 |
| `npm test` | 5 个测试文件，18 项通过 |
| `npm run build` | 用户端、管理端均构建成功；用户端 PWA 生成成功 |
| `npm run test:e2e`，`PLAYWRIGHT_CHANNEL=chrome` | 8 项浏览器契约测试通过 |
| `python -m ruff check .` | 后端检查通过 |
| `python -m ruff format --check .` | 194 个 Python 文件格式通过 |
| `python -m pytest --no-db -o addopts= -q` | 498 项通过，201 项跳过 |
| PowerShell 语法解析 | setup.ps1、dev.ps1 通过 |
| YAML 解析 | Compose 主文件和 GitHub Actions 文件通过解析 |
| `python scripts/precommit_frontend.py` | JavaScript 检查钩子通过 |

浏览器契约测试覆盖天气展示与移动导航、登录跳转、SSE 聊天、AI 行程草案与显式保存、偏好保存后刷新、接口错误重试、路线与穿搭、其余业务页面加载。API 响应由测试拦截，不代表真实 LLM 或数据库联调通过。

另行完成管理端浏览器冒烟检查：登录页显示、回车提交、普通用户令牌被清除并返回登录页，无无限重定向和浏览器运行时异常。管理端修改后重新构建成功。

## 真实接口与页面

- 已运行 FastAPI：根路径、OpenAPI 文档、区县列表成功返回；OpenAPI 包含 80 个路径，区县列表包含 11 个区。
- 已真实调用 Open-Meteo，获取广州和天河区天气；页面成功显示实时温度与未来天气。
- Chrome 桌面 1440px 和手机 390px 页面已截图检查；页面无运行时异常，手机页面无横向溢出，导航可折叠。
- 天气时间统一显示北京时间，支持 UTC 带时区与本地无时区返回格式。

## 2026-10-09 尚未验证的部分

- 本机没有可用的 Docker / PostgreSQL / Redis 服务，数据库迁移和 201 项依赖数据库的测试尚未执行。`--no-db` 显式跳过数据库相关用例，默认 pytest 行为仍要求真实测试库。
- `/health` 因 PostgreSQL 和 Redis 不可用而返回 503；不能据此宣称全系统健康。
- 未配置真实模型、高德、SMTP、Web Push 等密钥，因此真实 Agent 对话、真实 AI 行程生成、通知投递和全部数据库 CRUD 联调尚未执行。
- Docker 镜像构建、完整 Compose 启动、生产覆盖文件和 GitHub Actions 尚未实际运行；YAML 解析不等于容器运行验证。
- 可选 OCR、文档解析、语音识别依赖未安装和联调；核心依赖已安装。
- 两端构建存在大于 500KB 的产物提示，主要涉及 UI / 图表依赖；构建通过，后续可按需加载优化。

启动依赖和配置步骤见 [LOCAL_SETUP.md](LOCAL_SETUP.md)。源代码中的上游署名与原仓库历史保留，未向上游推送改动。

## 2026-10-10：修复业务表迁移遗漏

本次环境已有 Docker PostgreSQL 17，包含 vector、PostGIS、TimescaleDB 扩展。开发库原处于 `0013_user_preferences`，但缺少 `user_knowledge`、`chat_messages`、`itineraries`、`clean_tasks`，导致个人知识库列表接口报表不存在。新增 `0014_missing_business_tables` 迁移，并提高 Alembic 最低版本至 1.13.3（支持创建表时的 `if_not_exists` 参数）。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 195 个文件通过 |
| 对新增迁移单独执行 Ruff 检查及格式检查 | 通过；迁移目录不在默认检查范围内 |
| `python -m pytest tests/api/test_migrations.py -o addopts= -q` | 2 项通过：空库迁移及知识库增删查、检索、用户隔离；旧库升级保留数据 |
| `python -m pytest` | 699 项通过，2 项失败，无跳过；执行了数据库集成测试 |
| `./scripts/dev.ps1 migrate` | 开发库成功升级到 `0014_missing_business_tables`，所有 ORM 模型对应表均存在 |
| 真实后端 `GET http://127.0.0.1:8000/api/v1/my-knowledge` | 使用本地用户 1 的令牌调用，HTTP 200、`code=0`、`total=0` |

完整套件的两项失败均位于 `tests/api/test_notifications.py`：`TestVapidKey::test_返回公钥` 假定已配置 VAPID 公钥，但本地配置为空；`TestPrefs::test_默认全开` 精确比对旧的四字段响应，实际接口已包含 `risk_enabled`。本次未修改通知接口或这些测试；不能把本次完整套件表述为全部通过。前端未改动，未重复运行前端检查；外部模型调用未在本次验证范围内。

## 2026-10-10：多智能体接入多轮聊天历史

复用已有 `chat_messages` 表及会话读写服务，无数据库结构变化。普通和 SSE 入口向多智能体传递当前用户、当前会话最近 10 条消息；领域、通用与汇总节点按原角色接收历史，LLM 路由使用历史理解省略问题。流式路径复用已计算的路由结果，包括空列表，避免重新路由后转发错误的节点。未修改本地 `.env`。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 196 个文件通过 |
| 多智能体、聊天历史服务和聊天 API 相关回归 | 59 项通过，包含 13 项新增回归 |
| `python -m pytest` | 712 项通过、2 项失败、无跳过；两项失败仍为上述通知用例 |
| 真实 HTTP + PostgreSQL + 已配置 DeepSeek | 在临时后端以 `AGENT_MODE=multi` 运行，普通接口发送“你好，我叫小林，请记住我的名字。”，同一会话流式追问名字，回答正确包含“小林”，收到 `done` |
| 对话落库与清理 | 真实接口查询到 4 条消息（两轮）；验证会话已删除，临时后端已停止 |

API 回归使用真实数据库和完整多智能体图，模型替身覆盖单领域、多领域汇总及通用回答，验证最近 10 条限制与用户/会话/匿名隔离。真实模型验证覆盖通用回答的两轮记忆；没有宣称全部外部工具联调通过。前端未改动，未重复前端检查。目前没有历史摘要，独立图片路径尚未接入历史。

## 2026-10-10：知识库 BM25 + Dense + RRF

公共和个人知识库改为 BM25 与 pgvector 双路召回，再按 RRF 融合排名。新增 `jieba` 中文分词依赖；默认每路召回 30 个候选、RRF 常数 60、最终 Top-K 默认 5。接口保留向量 `similarity`，增加 BM25、RRF 分数与各路排名；个人知识库页面和 Agent 工具使用融合分数。按用户最新要求，本次不做 Reranker。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 199 个文件通过 |
| 混合检索单元/API 用例及迁移回归 | 23 项通过（21 项新增回归，2 项迁移回归） |
| `python -m pytest` | 733 项通过、2 项失败、无跳过；仍为上述两项通知用例 |
| `npm run check:js` | 通过 |
| `npm --prefix frontend-user run lint` | 通过 |
| `npm test` | 5 个文件、18 项通过 |
| `npm run build` | 用户端和管理端均通过，存在原有大产物提示 |
| `npm run test:e2e`，Chrome | 8 项通过，使用 mock API 的浏览器契约验证 |
| 真实 `POST /api/v1/my-knowledge/search` | HTTP 200，测试资料两路均排名 1；`bm25_score=0.395563`、`similarity=0.6956`、`rrf_score=0.03278689` |
| 真实 `GET /api/v1/knowledge/search` | HTTP 200，未返回同一用户的私有测试资料；测试资料已按唯一标题删除 |

回归验证了 BM25 手算分数、词频饱和与长度惩罚、RRF 按排名累加及片段去重、中文与编号检索、仅关键词召回、零向量、删除后结果变化、公共/个人/不同用户隔离和 API 分数字段。测试中的 Embedding 使用替身；本地未配置有效智谱 Embedding Key，真实接口使用现有字符哈希向量兜底，不能据此宣称外部 Embedding 模型质量通过。无需新增数据库表或迁移，本地 `.env` 未修改。BM25 当前逐次读取当前范围文本并在线评分，规模限制见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

## 2026-10-10：gte-rerank-v2 重排序

公共和个人知识库在 RRF 之后接入百炼原生文本排序 API，默认对融合前 30 条候选评分，再取最终 Top-K。保留原有分数，新增 `rerank_score`、`rerank_rank`；个人页面及 MCP/本地工具优先使用重排分数。未启用、无百炼 Key、模型超时或无效响应时整批保留 RRF 顺序，不填充假分数。无新增依赖或数据库迁移；未修改本地 `.env`。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 201 个文件通过；仅开始本次任务前已存在的 `embedding_client.py:81` 行尾空格未通过，保留该本地编辑 |
| 本次修改/新增的 12 个 Python 文件单独格式检查 | 通过 |
| 混合检索、重排序及迁移相关回归 | 59 项通过，其中 36 项新增重排回归 |
| `python -m pytest` | 769 项通过、2 项失败、无跳过；失败仍是前述 VAPID 公钥和默认通知偏好用例 |
| `npm run check:js`、用户端 lint | 通过 |
| `npm test` | 5 个文件、18 项通过 |
| `npm run build` | 用户端及管理端均通过，保留原有大产物提示 |
| `npm run test:e2e`，Chrome | 9 项通过；新增知识库检索验证模型分数优先、零分与 null 回退 |
| 真实个人知识库上传/检索 | 上传 HTTP 201、检索 HTTP 200；唯一测试资料命中，`rrf_score=0.03278689`、`similarity=0.5971`、`rerank_score=null`、`rerank_rank=null` |
| 真实公共知识库检索及测试资料清理 | 公共 GET HTTP 200，未返回私有测试资料；唯一标题删除 HTTP 200 |

本地已配置智谱 Embedding Key，但尚未配置 `DASHSCOPE_API_KEY` 或 `QWEN_API_KEY`，因此真实后端验证覆盖无百炼 Key 的 RRF 回退，未验证真实 `gte-rerank-v2` 账号可用性或排序质量。真实数据库 API 回归使用 `respx` 的百炼响应替身，验证候选排序在 Top-K 截断之前执行、排序索引准确映射回本地内容、其他用户和公共资料不发送到私有排序请求，以及鉴权/限流/服务错误/超时/不完整或非法结果回退。浏览器测试使用 mock API。配置真实百炼 Key 与正确地域/空间 API 基址并重启后，可通过非 null 的 `rerank_score` 确认模型排序成功，详见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

## 2026-10-10：14 组真实模型问答及公共知识库迁移修复

用户配置百炼 Key 后，先直接验证智谱 `embedding-3`（1024 维）与百炼 `gte-rerank-v2` 均真实 HTTP 200。公共知识库原有 0 条资料；建立真实索引发现 `0001_initial` 未建立模型声明的 `(source, chunk_index)` 唯一约束，导致 upsert 失败。新增并应用 `0015_knowledge_chunk_unique`，保留已有数据及 ORM 同名约束；旧数据重复时由数据库拒绝并回滚，不自动删资料。

| 检查 | 结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 201 个文件通过，仅任务前已有的 `embedding_client.py:81` 行尾空格未通过，保留本地编辑 |
| 新迁移单独 Ruff 与格式检查 | 通过；Alembic 目录不在默认检查范围内 |
| `tests/api/test_migrations.py` | 2 项通过，增加公共库唯一约束、重复及并发 upsert、已建 ORM 约束兼容的实际数据库断言 |
| `python -m pytest` | 769 项通过、2 项失败，无跳过；仍为上述两项通知用例 |
| 实际开发库升级与建立索引 | 修订为 `0015_knowledge_chunk_unique`；16 个文档、33 个分块，使用真实 Embedding 成功写入并保留 |
| 14 组真实模型检索与回答 | 知识库 API 业务 `code=0`，各返回 5 条真实重排结果；DeepSeek 回答非空，无 Embedding/Reranker 回退 |
| 12 个范围内问题的相关来源前 3 条命中 | 重排前后均为 12/12；3 个问题第一条片段改变，不能由此断言显著准确率提升 |
| 2 个资料不足问题 | 故宫实时余票和私人餐厅笔记，模型均明确说明无法确认，没有虚构答案 |
| 实际运行的 HTTP 后端与聊天接口 | 公共搜索 HTTP 200，重排分数非 null；匿名聊天早茶/广州塔 2 次均 HTTP 200、`code=0`、`intent=knowledge` |

14 组测试使用 FastAPI ASGI 直连、真实 PostgreSQL、真实模型 API；通过项目 `llm_client` 将检索上下文交给 DeepSeek，未执行 Agent 工具决策流程。额外 2 次 `/api/v1/chat` 使用正在运行的 HTTP 服务，覆盖当前 single Agent 入口。资料问答批次采用更严格的依据资料提示词；聊天回答仍有少量未直接被资料支持的补充建议，详见 [完整测试报告](RAG_LIVE_TEST_2026-10-10.md)。匿名聊天不写历史，无临时私有资料或临时服务器；本地 `.env` 与既有 Embedding 客户端编辑保持不变。
