# 当前架构

用户端和管理端均为 Vue 3 + JavaScript + Vite。后端保留 Python + FastAPI + SQLAlchemy + Alembic；PostgreSQL 17 仍使用 TimescaleDB、PostGIS、pgvector 扩展。

## 请求链路

Vue 页面 → api/*.js → FastAPI routers → services / skills → PostgreSQL 或外部服务。

AI 对话 → agent.py（single）或 multi_agent.py（multi）→ LangChain 模型工具调用 → MCP 公共工具 / 本地用户工具 → SSE 返回前端。默认 single，AGENT_MODE=multi 启用领域编排。

## 前端目录

- pages/*.vue：按路由拆分的业务页面，路由懒加载。
- components/*.vue：页面标题、任务状态、Markdown、地点输入、行程草案、预警监听。
- api/*.js：统一 HTTP 与原有领域接口，响应已经解包一次。
- stores/auth.js：响应式登录状态；composables/useTask.js：可重试加载状态。
- utils/sse.js：支持 UTF-8 跨块、CRLF 和流尾事件的读取器。
- PWA 只缓存静态应用外壳，用户 API 不进入 Service Worker 缓存。

单智能体和多智能体文本对话均复用 PostgreSQL 的 `chat_messages`：登录用户每轮保存提问与最终回答，后续请求按 `user_id + conversation_id` 读取最近 10 条消息（通常约 5 轮），按时间正序送给模型。多智能体把这份历史传给调度的 LLM 路由、各领域节点、通用回答和汇总节点；关键词路由仍只匹配本次问题，避免重跑历史问题。普通接口和 SSE 接口行为一致，匿名用户不保存或读取历史。

聊天消息存在 PostgreSQL，并不等于 LangGraph checkpoint 持久化。目前没有旧对话自动摘要；图片对话仍走独立视觉路径，尚未接入历史。天气共享快照和逐小时自动重规划仍属于后续增强方向。

部署采用 docker-compose.yml；migrate 服务先执行 Alembic，API / Celery 等待迁移成功。开发代理统一到 8000，用户端 5173，管理端 5174。
