# Checkpointer 持久化验证记录

## 本地配置与迁移

- 安装 `langgraph-checkpoint-postgres 3.1.2`、`psycopg 3.3.6` 和 `psycopg-pool 3.3.3`；`python -m pip check` 通过。
- 开发数据库执行 `python -m alembic upgrade head` 成功，实际版本为 `0018_agent_checkpoints`。
- 本地 `.env` 未修改；`CHECKPOINT_ENABLED` 默认 true，重启后端后新代码生效。

## 自动测试

backend 中最终相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api/test_checkpoints.py tests/api/test_migrations.py tests/api/test_chat_history.py tests/api/test_memory.py tests/api/test_chat_summary.py tests/unit/test_multi_agent.py tests/unit/test_chat_history_service.py -o addopts= -q
```

**113 passed，82.59 秒**。包括 19 个新增检查点用例，以及从空库迁移后使用官方 saver 恢复的新增用例。使用真实 PostgreSQL，未使用 `--no-db`，没有跳过数据库集成测试；模型/外部工具使用替身。

覆盖卡片+历史 RAG+检查点共同使用、普通/SSE 两模式、摘要与未摘要历史、reducer 刷新、工具步骤后中断、并行 pending writes、图完成但消息保存失败、重复恢复幂等、空模型答案可重试、模式变更后恢复、跨模式未完成任务阻断、用户/会话隔离、匿名/禁用回退、并发请求和删除冲突、首轮未完成任务删除、会话和用户删除级联。

另执行完整 `python -m pytest`：**905 passed，2 failed，236.05 秒，覆盖率 65%**。这次完整运行之后补上空回答和删除数量边界，最终由上述 113 项相关回归验证。两个完整套件失败均来自原有通知测试，与本次检查点功能无关：

- `tests/api/test_notifications.py::TestVapidKey::test_返回公钥`：本地 VAPID 公钥为空，旧断言要求长度超过 40。
- `tests/api/test_notifications.py::TestPrefs::test_默认全开`：接口已有 `risk_enabled` 字段，旧测试仍只比较四个字段。

`python -m ruff check .` 全部通过。`python -m ruff format --check .` 仅发现原有 `app/services/embedding_client.py:81` 行尾空格，其他 229 个文件格式通过；该文件在任务开始前已存在本地改动，未修复、未加入本次提交。前端未改动，没有重复执行前端构建和浏览器 mock 回归。

## 真实 API / 模型

**8 个问题完成，14 项检查通过**：真实 DeepSeek、智谱 `embedding-3`（1024 维）、配置的百炼 `gte-rerank-v2`，经 FastAPI ASGI API 和开发 PostgreSQL 联调。原始问题、回答、耗时与断言保留在 [JSON](CHECKPOINT_LIVE_RESULTS_2026-10-11.json)，场景与测试限制见 [真实模型记录](CHECKPOINT_LIVE_TEST_2026-10-11.md)。没有强杀进程，也没有把故障注入替身测试称为真实模型故障测试。

验收结束后二次查询开发库，临时 `cp_live_*` 账号数为 0；`agent_checkpoint_threads`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 均为 0。迁移版本与官方版本记录表保留，真实用户数据未清空。
