# Checkpointer + 双层记忆：真实模型验收

2026-10-11 14:35:04～14:36:14（Asia/Shanghai）在本机执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.scripts.verify_checkpoint_memory --output ../docs/CHECKPOINT_LIVE_RESULTS_2026-10-11.json
```

使用开发 PostgreSQL、真实 DeepSeek 对话模型、智谱 `embedding-3`（1024 维）和配置的百炼 `gte-rerank-v2`，没有 mock 这些模型。通过 FastAPI ASGI 路由执行，单、多 Agent 各 4 个问题，共 8 个问题全部完成。14 项状态/记忆/清理检查全部通过，临时合成账号及其消息、记忆、检查点最终删除。

| 验证 | single | multi |
| --- | --- | --- |
| 用户说长期住天河、不吃辣 | 模型确认 | 模型确认 |
| 跨会话回忆旧活动集合暗号，同时读饮食卡片 | 青鹭5816、不吃辣 | 紫鸢9632、不吃辣 |
| SSE 追问饮食与暗号 | 正确、收到 done | 正确、收到 done |
| 继续问居住区域 | 天河区 | 天河区 |
| 保存的图背景同时包含卡片与历史 RAG | 通过 | 通过 |
| 暗号未提取为卡片、从历史找回 | 通过 | 通过 |
| 新建 saver/图恢复已完成状态，两次恢复不增加消息 | 通过 | 通过 |
| 检查点状态为 saved、删除会话清理线程 | 通过 | 通过 |

暗号通过合成用户原话预置于旧会话，并追加 12 条无关消息，使其超出最近十条窗口；没有把暗号作为人工卡片注入。完整提问、回答、耗时及逐项布尔检查见 [原始 JSON](CHECKPOINT_LIVE_RESULTS_2026-10-11.json)。

此次真实模型验收验证的是新建图和数据库连接恢复，没有强杀后端进程，也没有故意破坏真实模型服务。节点执行中断、工具节点不重跑、并行成功领域 pending writes 保留、图结束但消息未提交等故障由 `tests/api/test_checkpoints.py` 在真实 PostgreSQL 上注入，使用模型替身；不能把这部分称为真实模型故障测试。
