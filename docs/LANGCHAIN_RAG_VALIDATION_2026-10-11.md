# LangChain RAG 验证记录（2026-10-11）

本次把共用混合检索入口接入 LangChain Retriever / EnsembleRetriever / ContextualCompressionRetriever。原来的公共/个人知识库 HTTP 接口、Agent 工具和历史 RAG 都使用该入口。1024 维 Embedding、已有业务表、返回字段、用户隔离与重排失败回退继续使用。

## 已完成的针对性验证

| 检查 | 实际结果 |
| --- | --- |
| `python -m ruff check .` | 通过 |
| 本次 6 个 Python 文件 `ruff format --check` | 通过 |
| 全库 `ruff format --check .` | 222 个文件通过；仅任务前已有的 `embedding_client.py:81` 行尾空格未通过，保留本地编辑 |
| `python -m pip check` | 通过，无依赖冲突 |
| 新 LangChain 单元用例 + 原 BM25/RRF 单元用例 | 17 项通过，`--no-db`；无数据库用例被收集或跳过 |
| 新 LangChain 数据库用例 | 5 项通过，真实 PostgreSQL |
| 全套中的检索、重排、记忆与迁移用例 | 87 项全部通过，包含上述新增用例 |
| `python -m pytest` | 838 项通过、2 项失败、无跳过；整体覆盖率 64% |

新用例证明调用 LangChain 原生 RRF 方法；正文相同但 ID 不同的片段不会合并；两路以异步屏障验证同时启动；callbacks 传播到两路；Document 保留正文、ID、null 来源和分数。还验证标准 Retrieval Chain / Stuff Documents Chain 可组合，以及空库不调用模型、历史排除消息与用户范围同时作用于两路和 reranker、非法/空/零查询向量保留 BM25 召回。

全套失败仍为原有通知用例 `TestVapidKey::test_返回公钥` 与 `TestPrefs::test_默认全开`，与先前验证记录一致；本次未修改通知逻辑。没有使用 `--no-db` 跳过完整回归中的数据库集成测试。

## 真实模型与 API

执行 `python -m app.scripts.verify_langchain_rag --output ../docs/LANGCHAIN_RAG_LIVE_2026-10-11.json`，原始结果见 [JSON](LANGCHAIN_RAG_LIVE_2026-10-11.json)。使用 FastAPI ASGI 直连接口、真实开发 PostgreSQL、智谱 `embedding-3`（1024 维）、百炼 `gte-rerank-v2` 和当前 DeepSeek 对话配置，没有替换模型响应。

| 场景 | 实际观察 |
| --- | --- |
| 公共 API：广州塔门票 | HTTP 200、`code=0`，3 条结果，第一条《广州必游景点推荐》，重排分数 0.194342 |
| 公共 API：广州早茶 | HTTP 200、`code=0`，3 条结果，第一条《广州地道美食指南》，重排分数 0.781730 |
| 公共 API：雨天室内活动 | HTTP 200、`code=0`，3 条结果，第一条《天气与出行规划建议》，重排分数 0.193788 |
| 私有 API：模拟订单 LC1011ABC | 上传 HTTP 201、检索 HTTP 200；仅返回本人测试资料，Dense/BM25 均第 1，RRF=0.03278689，重排分数 0.899152 |
| 其他用户查询同一订单 | HTTP 200、空列表，未泄露私有资料 |
| 历史 Retriever：模拟暗号 | 排除旧暗号消息后只返回蓝鲸1011；Dense/BM25 均第 1，重排分数 0.962550 |
| 原生 LangChain 资料问答链 | Retriever → Stuff Documents Chain → 真实 DeepSeek；回答“最晚退票时间为活动当天上午10:30，手续费为12元。”，与模拟资料一致 |

共 7 个验收场景，其中只有最后一个包含回答模型生成；不把检索检查表述为 7 次聊天。Embedding/rerank 调用日志为 HTTP 200，所有非空检索都有真实重排分数，模型客户端回退警告为 0。临时合成账号及其私有文档/历史消息/历史切片已清理；另行查询 `langchain_live_%` 测试账号计数为 0。公共原有资料未修改，`.env` 未修改。

前三个问题检查接口和真实模型链路是否可用，没有建立相关性标注，也没有比较迁移前后排序质量。较低的 rerank 分数如实保留，不等于回答准确率。最后一个是合成资料上的受控数字核对，不代表模型普遍不会出错。该验收直接组合原生资料问答链，没有重新测试整个 single/multi Agent 工具决策流程。

## 运行方式

本次依赖已安装。其他环境执行 `pip install -r backend/requirements.txt` 后重启后端/worker/MCP；不用重新生成知识库向量或新增迁移。实际实现和设计边界见 [LANGCHAIN_RAG.md](LANGCHAIN_RAG.md)。前端未改动，本次没有重复前端构建、lint 或浏览器契约测试。
