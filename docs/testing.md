# 测试

前端：`npm --prefix frontend-user run lint`、`npm test`、`npm run build`。根目录 `npm run check:js` 检查两端没有 TS 源文件。

浏览器：`npm run test:e2e`。首次安装 Chromium：在 frontend-user 执行 `npx playwright install chromium`。可在 PowerShell 设置 `$env:PLAYWRIGHT_CHANNEL = 'chrome'` 使用现有 Chrome。

当前浏览器回归覆盖天气字段与移动布局、登录跳转、SSE 对话、草案生成后显式保存、偏好保存回读、错误重试、路线穿搭和各页面启动。API 在测试中按契约拦截，不代替真实数据库/模型验证。截图与 trace 放在 test-results，避免加入 Git。

后端：backend 目录执行 `python -m pytest`；完整套件依赖 PostgreSQL（vector / PostGIS）。测试库独立命名为 weather_db_test，会重建表。

`tests/api/test_migrations.py` 额外创建名称为 `weather_migration_<随机值>_test` 的临时数据库，用 Alembic 从空库迁移到最新版本，验证所有模型表存在及个人知识库接口的增删查、向量检索和用户隔离。另一个用例验证从旧版本升级时保留 ORM 已建表中的数据。测试结束删除临时库；测试账号需有创建数据库权限，迁移还需要 TimescaleDB 扩展。该回归不依赖 `create_all` 初始化，能发现迁移漏建表的问题。

该迁移回归还验证公共知识库的 `(source, chunk_index)` 唯一约束、重复 upsert 更新正文与元数据、两个独立会话并发 upsert 只留下一个分块，以及旧库已有 ORM 同名约束时正常升级。

无数据库的本地验证使用 `python -m pytest --no-db -o addopts= -q`。该模式显式跳过依赖 db/client 等集成夹具的用例，并禁止初始化/清理数据库。默认模式继续运行完整套件，CI 仍使用真实 PostgreSQL。

每次验证的实际通过数、跳过数、环境限制记录在 VALIDATION.md。不能把 no-db 或前端 mock 测试表述成全栈联调成功。

多智能体多轮对话回归：`python -m pytest tests/api/test_chat_history.py tests/unit/test_multi_agent.py tests/unit/test_chat_history_service.py -o addopts= -q`。API 用例使用真实 PostgreSQL 和完整 LangGraph 图，模型与外部工具使用替身，覆盖普通及 SSE 两轮对话、领域与汇总节点接收历史、通用回答、最近 10 条限制、用户/会话隔离和匿名请求。单元用例另覆盖省略问题的上下文路由及工具循环不改写共享历史。

混合检索回归：`python -m pytest tests/unit/test_hybrid_retrieval.py tests/api/test_hybrid_knowledge.py tests/api/test_migrations.py -o addopts= -q`。包含 BM25 数学性质、中文及编号分词、RRF 融合和去重、两路互补、无向量召回、用户/公共私有隔离、删除后结果变化及 API 返回分数字段；数据库查询真实执行，Embedding API 使用替身。详细链路见 [RAG_RETRIEVAL.md](RAG_RETRIEVAL.md)。

重排序回归：`python -m pytest tests/unit/test_rerank_client.py tests/api/test_rerank_knowledge.py -o addopts= -q`。真实数据库和 API 链路接入百炼 HTTP 替身，覆盖请求格式、输入索引映射、最终 Top-K 之前重排、私有候选隔离、关闭/无 Key、超时、401/429/503、无效响应和异常分数降级。测试全局默认关闭外部重排，新用例显式开启并通过 `respx` 拦截，开发配置不触发真实模型调用。模型能力和账号可用性仍须通过配置真实 Key 的接口联调确认。
