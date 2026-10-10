# 本地开发：Python + FastAPI + PostgreSQL + Vue JavaScript

当前版本基于 Ticnix/weather-travel-recommend-system 重构。用户端和管理端均采用 Vue 3 + JavaScript；保留 Python 后端的 LangChain、LangGraph、MCP、RAG 和业务接口。

## 先准备环境

- Node.js 22.12+（本次开发使用 Node 24）。
- Python 3.12 或 3.13。
- Docker Desktop：推荐用项目 Compose 启动 PostgreSQL 和 Redis。
- 使用生产覆盖文件中的 `!reset` 时，需要支持该语法的新版 Docker Compose。
- PostgreSQL 17 需要 TimescaleDB、PostGIS、pgvector 扩展，普通空白 PostgreSQL 实例不能直接执行本项目全部迁移。

在项目根目录打开 PowerShell：

```powershell
.\scripts\setup.ps1
docker compose up -d --build postgres redis
.\scripts\dev.ps1 migrate
```

setup 会创建 backend/.venv、安装依赖，首次生成 backend/.env 和随机 JWT 密钥；已存在的配置不会被覆盖。需要 PDF/Word/Excel、离线 OCR 或本地语音识别时使用 `setup.ps1 -Media`。没有配置模型密钥时，AI 对话和行程生成不能正常调用模型，天气首页仍可通过 Open-Meteo 获取公开天气。

更新代码后也要执行 `./scripts/dev.ps1 migrate`。若出现 `relation "user_knowledge" does not exist`，在项目根目录执行此命令；`0014_missing_business_tables` 会补齐个人知识库、聊天记录、行程和清洗任务表，并保留已存在的表和数据。无需删除数据库或手动运行 `create_all`。

公共知识库建立索引时若提示 `there is no unique or exclusion constraint matching the ON CONFLICT specification`，同样执行迁移命令。`0015_knowledge_chunk_unique` 补齐 `(source, chunk_index)` 唯一约束，让重复/并发建索引可以更新同一分块；已有同名 ORM 约束时跳过。迁移不删除资料，旧库若有重复来源分块会报错并回滚，需要先核查重复数据。

分别在三个终端运行：

```powershell
.\scripts\dev.ps1 backend
.\scripts\dev.ps1 user
.\scripts\dev.ps1 admin
```

地址：用户端 http://127.0.0.1:5173，管理端 http://127.0.0.1:5174，后端文档 http://127.0.0.1:8000/docs。

定时提醒还需要另外两个终端：`dev.ps1 worker` 与 `dev.ps1 beat`。Windows worker 使用 solo 池；生产使用容器 worker。

## 配置 backend/.env

| 配置 | 用途 |
| --- | --- |
| DB_URL、REDIS_URL | 本地数据库和缓存连接，Compose 内部会覆盖主机地址 |
| DEEPSEEK_API_KEY | 默认对话模型与行程生成；可以按 config.py 切换其他模型提供商 |
| AGENT_MODE | single：单 Agent 工具循环；multi：Supervisor + 领域 Agent |
| AMAP_API_KEY | 真实路线和地点查询 |
| ZHIPU_API_KEY | 语义向量与默认视觉模型；未配置时的本地向量回退不等同于语义模型 |
| DASHSCOPE_API_KEY | 百炼 `gte-rerank-v2` 重排序；为空时尝试 QWEN_API_KEY，与智谱 Key 分开 |
| RERANK_BASE_URL | 百炼原生 API 基址，结尾为 /api/v1；工作空间 Key 使用对应空间及地域的地址 |
| WEATHER_PROVIDER | 默认 open_meteo；配置和风后可改 qweather |
| QWEATHER_API_KEY、QWEATHER_BASE_URL | 和风 API 凭据和账号对应的服务地址 |
| TAVILY_API_KEY | 联网搜索，未配置时尝试项目原有回退源 |

真实密钥只写在 backend/.env，不能写入前端。语音默认关闭；按需安装 requirements-media.txt 后再配置 ASR_PROVIDER。通知邮箱、Web Push 需要另外配置 SMTP/VAPID，通知中心会显示通道是否就绪。

使用多智能体多轮文本对话时，设置 `AGENT_MODE=multi` 并重启后端。登录后在同一个会话中继续提问；后端自动保存每轮提问和最终回答，并读取最近 10 条历史消息。客户端调用 `/api/v1/chat` 或 `/api/v1/chat/stream` 时，需要持续传入相同的 `conversation_id`，普通接口会返回该 ID；流式客户端应自行生成并复用。新会话和不同用户的历史互不共享，匿名对话不持久化；目前未做历史摘要。

知识库默认使用 BM25 + 向量混合检索、RRF 融合及 `gte-rerank-v2` 重排序，包含 `jieba` 中文分词依赖。配置百炼 Key 并重启后端、运行中的 worker 和独立 MCP 服务即可启用重排，不需要安装 DashScope SDK 或新增迁移。缺少百炼 Key 或排序失败时返回 RRF 结果。仅为启用 BM25/重排序无需重新生成向量；从本地兜底向量切换至智谱真实 Embedding 时，仍需重建已有资料向量。参数、配置示例和返回分数含义见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

## 双层长期记忆

双层长期记忆默认开启，复用已配置的对话模型、智谱 Embedding 和百炼重排序；无需新 Key。更新后执行 `./scripts/dev.ps1 migrate` 创建 `0016_chat_memory` 并重启后端。旧聊天可在 `/docs` 登录后调用 `POST /api/v1/memory/backfill` 分批补索引，直到 `has_more=false`。配置、卡片查看和清空方式见 [MEMORY.md](MEMORY.md)。

## 全容器运行

先运行 setup 或按 backend/.env.example 创建 backend/.env，然后：

```powershell
docker compose up -d --build
```

Compose 中 migrate 服务先完成 Alembic 迁移，后端和任务进程再启动。用户端 http://localhost:8080，管理端 http://localhost:8081。首次构建 PostgreSQL 扩展较慢。容器默认只安装核心后端依赖；多模态依赖可用 `docker compose build --build-arg INSTALL_MEDIA=true backend celery celery-beat migrate` 构建。

首次注册普通用户后，如需管理权限，可在自己管理的数据库中将该账号角色改为 admin：

```sql
UPDATE users SET role = 'admin' WHERE username = '你的用户名';
```

## 检查与测试

```powershell
npm run check:js
npm --prefix frontend-user run lint
npm test
npm run build
cd frontend-user
npx playwright install chromium
npm run test:e2e
```

浏览器回归使用与后端响应结构对应的测试数据，覆盖天气、登录、流式聊天、生成草案与显式保存、偏好持久化、异常重试及移动导航；测试数据仅在 e2e 文件中，不进入正式应用。它们不能代替真实数据库和外部服务联调。

后端完整测试需要可用的 PostgreSQL（测试会重建名称以 `_test` 结尾的专用测试库）：

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

没有数据库时，可以使用 `python -m pytest --no-db -o addopts= -q` 只运行不依赖数据库的测试。本次实际结果和未验证项见 [VALIDATION.md](VALIDATION.md)。

## 重构边界

- 所有现有业务接口继续保留；用户界面用 Vue 重写，管理端 Vue 脚本去掉 TS。
- 聊天支持 SSE、停止生成、历史对话、附件和浏览器录音；附件解析能力取决于服务端可选依赖与模型配置。
- 提供天气、路线/穿搭、AI 行程、行程 CRUD/日历导出/海报、笔记、知识库、资讯、通知和个人偏好页面。
- 后端原有单/多 Agent 行为保留。本次没有把 PG 聊天记录改造成 LangGraph checkpoint，也没有新增逐小时自动重规划算法。
- 旧 React 组件测试由 Vue 相关测试替换；历史文档和博客仍记录上游原版本，当前启动方式以本文为准。
