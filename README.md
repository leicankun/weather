# 知天气 · 天气与出行 Agent

基于 [Ticnix/weather-travel-recommend-system](https://github.com/Ticnix/weather-travel-recommend-system) 重构的天气出行助手。

**后端：Python + FastAPI + PostgreSQL。前端：Vue 3 + JavaScript（用户端与管理端均无 TypeScript）。**

## 快速开始

Windows PowerShell，在项目根目录运行：

```powershell
.\scripts\setup.ps1
docker compose up -d --build postgres redis
.\scripts\dev.ps1 migrate
```

分别在终端启动：

```powershell
.\scripts\dev.ps1 backend
.\scripts\dev.ps1 user
.\scripts\dev.ps1 admin
```

- 用户端：http://127.0.0.1:5173
- 管理端：http://127.0.0.1:5174
- 后端文档：http://127.0.0.1:8000/docs

完整环境说明、模型密钥、容器启动与可选多模态依赖见 [本地启动指南](docs/LOCAL_SETUP.md)。已执行的检查及未验证项见 [验证记录](docs/VALIDATION.md)。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 用户端 | Vue 3、JavaScript、Vite、Vue Router、Pinia、Element Plus |
| 管理端 | Vue 3、JavaScript、Element Plus、ECharts |
| 后端 | Python、FastAPI、Pydantic、SQLAlchemy、Alembic |
| 数据库 | PostgreSQL 17，保留 TimescaleDB / PostGIS / pgvector 扩展 |
| AI | LangChain、LangGraph、MCP、RAG、业务 Skill |
| 后台任务 | Redis、Celery、Celery Beat |
| 数据源 | Open-Meteo / 和风天气、高德地图、联网搜索 |

## 功能

- 当地天气、7 天预报、登录后的逐小时天气与 AQI。
- AI 对话：SSE 流式输出、停止生成、历史会话、附件和录音入口。
- 双层长期记忆：偏好与事实 JSON 卡片 + 历史 RAG，单/多智能体共用，支持跨会话回忆；详见 [记忆说明](docs/MEMORY.md)。
- 路线与穿搭建议；AI 行程生成、天气规则审计、用户确认后保存。
- 我的行程：新增、编辑、删除、风险提醒、日历导出、分享海报。
- 旅行笔记、个人知识库、气象资讯、历史趋势。
- 用户偏好、通知订阅、邮件绑定、反馈；管理端保留数据和内容管理。

外部模型、地图、推送和附件解析需要相应配置，应用不会把测试数据作为真实结果展示。

## 目录

```text
backend/          Python API、Agent、工具、业务服务、数据库迁移与测试
frontend-user/    Vue + JavaScript 用户站
frontend-admin/   Vue + JavaScript 管理后台
scripts/          Windows 开发启动、JS 检查与原有部署脚本
docs/             当前架构、启动指南、验证记录及上游历史资料
```

## 验证命令

```powershell
npm run check:js
npm --prefix frontend-user run lint
npm test
npm run build
npm run test:e2e
```

浏览器测试首次运行需在 frontend-user 中执行 `npx playwright install chromium`；也可设置 `PLAYWRIGHT_CHANNEL=chrome` 使用已安装的 Chrome。

后端完整测试：在 backend 目录运行 `.\.venv\Scripts\python.exe -m pytest`，需要 PostgreSQL。无数据库时可用 `.\.venv\Scripts\python.exe -m pytest --no-db -o addopts= -q` 验证独立逻辑；数据库依赖用例会显式跳过，不能视为全量通过。

原项目的历史文档、博客和设计说明保留用于学习。它们可能提到旧 React / TypeScript 实现，当前架构以 README 与 docs/LOCAL_SETUP.md 为准。
