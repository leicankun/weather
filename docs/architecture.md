# 当前架构

用户端和管理端均为 Vue 3 + JavaScript + Vite。后端保留 Python + FastAPI + SQLAlchemy + Alembic；PostgreSQL 17 仍使用 TimescaleDB、PostGIS、pgvector 扩展。

## 请求链路

Vue 页面 → api/*.js → FastAPI routers → services / skills → PostgreSQL 或外部服务。

AI 对话 → agent.py（single）或 multi_agent.py（multi）→ LangChain 模型工具调用 → MCP 公共工具 / 本地用户工具 → SSE 返回前端。默认 single，AGENT_MODE=multi 启用领域编排。

多智能体专业领域回答新增核验：并行领域工具证据合并，多领域先汇总、单领域跳过汇总，再交给独立 `verify` 节点核对问题与原始证据。最多修正一次并复查，核验故障或仍不通过时不发布草稿。有专业领域的 SSE 等核验完成再发送完整答案；通用闲聊继续流式。核验复用默认模型，并非外部事实认证；详情见 [AGENT_VERIFICATION.md](AGENT_VERIFICATION.md)。

公共、个人知识库与历史记忆共用 LangChain 混合检索：BM25 Retriever + 自定义 pgvector Retriever 双路并行召回，经原生 EnsembleRetriever 做 RRF 融合，再通过 ContextualCompressionRetriever 和百炼 `gte-rerank-v2` compressor 重排。使用现有业务表与智谱 1024 维向量；BM25 保留中文搜索分词与正 IDF。私有语料、两路检索和发送到排序模型的候选均按用户隔离。每路默认召回 30 条，RRF 前 30 条参与重排，最后取 Top-K；未配置百炼 Key、显式关闭或调用失败时保留 RRF 顺序。组件及调用方式见 [LANGCHAIN_RAG.md](LANGCHAIN_RAG.md)，配置、分数含义和规模限制见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

公共与个人文档在 Embedding 前共用标题分块器：Markdown ATX 标题是硬边界，chunk 和 overlap 均不跨标题；区域内部开最多 300 字符窗口，按段末／句末回退，只有超长单元素才硬切。默认重叠最多 50 字符，优先保留完整段／句。实现与已有索引更新方式见 [CHUNKING.md](CHUNKING.md)。

## 前端目录

- pages/*.vue：按路由拆分的业务页面，路由懒加载。
- components/*.vue：页面标题、任务状态、Markdown、地点输入、行程草案、预警监听。
- api/*.js：统一 HTTP 与原有领域接口，响应已经解包一次。
- stores/auth.js：响应式登录状态；composables/useTask.js：可重试加载状态。
- utils/sse.js：支持 UTF-8 跨块、CRLF 和流尾事件的读取器。
- PWA 只缓存静态应用外壳，用户 API 不进入 Service Worker 缓存。

单智能体和多智能体文本对话均复用 PostgreSQL 的 `chat_messages`：登录用户每轮保存提问与最终回答。短期上下文默认读取本会话滚动摘要和全部尚未摘要的原始消息；每五轮检查一次，短期历史超过 6000 字符才调用默认模型压缩旧消息，最近 10 条（通常约 5 轮）保留原文。`chat_summaries` 按 `user_id + conversation_id` 保存摘要、覆盖水位和检查进度，两次更新之间的过渡消息不会被截掉。多智能体把同一份背景传给 LLM 路由、领域、通用、汇总和核验节点；关键词路由仍只匹配本次问题。普通接口和 SSE 接口行为一致，匿名用户不保存或读取历史。配置和故障边界见 [CHAT_SUMMARY.md](CHAT_SUMMARY.md)。

登录用户新增两层长期记忆：JSONB 偏好/事实卡片每轮有限注入；需要回忆时，从独立历史库以 BM25 + 向量 + RRF + reranker 检索。单/多智能体在入口共用读取与写入，SSE done 在写入完成或超时降级后发出；原始一轮消息在同一事务保存。按用户隔离、来源外键级联删除与清空水位避免旧记忆复活，详见 [MEMORY.md](MEMORY.md)。

聊天消息和短期摘要存在 PostgreSQL，并不等于 LangGraph checkpoint 持久化。摘要限当前会话，不升级为跨会话长期卡片；图片对话仍走独立视觉路径，可注入两层长期记忆背景，但尚未把短期历史作为多模态消息接入。天气共享快照和逐小时自动重规划仍属于后续增强方向。

部署采用 docker-compose.yml；migrate 服务先执行 Alembic，API / Celery 等待迁移成功。开发代理统一到 8000，用户端 5173，管理端 5174。
