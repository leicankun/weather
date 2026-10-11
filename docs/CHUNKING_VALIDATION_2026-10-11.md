# 标题硬边界分块验收（2026-10-11）

## 范围

共享 `knowledge_loader.split_text` 改为 ATX 标题硬边界、300 字符窗口内按段／句回退、超长元素才硬切。同标题内重叠最多 50 字符，优先重复完整软元素。公共加载器和个人上传均通过该入口。

## 针对性回归

backend 中执行：

```powershell
python -m pytest tests/unit/test_knowledge_chunking.py tests/api/test_chunking_knowledge_api.py -o addopts= -q
```

最终 **50 passed**：48 个单元案例和 2 个真实 PostgreSQL 集成案例。覆盖全部 ATX 层级、前言／相邻标题、全文不足窗口仍隔离、代码围栏内伪标题、非 ATX 行、CRLF、段／句回退、句尾闭引号、小数点、满窗口元素后的分隔空白、软重叠减少、超长元素硬切重叠、标题不泄漏和随机混合长短文本内容覆盖。集成用例验证公共 Embedding 输入与入库内容一致、个人上传后的块与序号、检索和删除。模型在自动回归中使用替身。

本次各数据库测试进程预建独立 `weather_chunking_<随机值>_test`，通过 `TEST_DB_URL` 指定；pytest 子进程结束后删除该库。未复用其他会话的测试库，未重建开发库，没有跳过 PostgreSQL 集成用例。

## 全量与静态检查

最终 `python -m pytest -o addopts= -q`：**888 passed，2 failed，14 warnings**，248.76 秒，无跳过。两个失败与任务前验证记录相同，均在 `tests/api/test_notifications.py`：`TestVapidKey::test_返回公钥` 因本地公钥为空，`TestPrefs::test_默认全开` 因响应新增 `risk_enabled` 字段而旧断言未同步。此次分块相关 50 项全部通过，未修改这两项无关通知行为。最后一次独立测试库已删除。

`python -m ruff check .` 通过；本次四个 Python 文件的 `ruff format --check` 通过。全目录格式检查仍发现本次修改之前已存在的 `app/services/embedding_client.py:81` 行尾空格，未更改或提交该本地编辑。本次涉及后端分块，没有前端改动。

## 真实 API 与模型

backend 中执行：

```powershell
python -m app.scripts.verify_chunking --output ../docs/CHUNKING_LIVE_2026-10-11_FINAL.json
```

使用 FastAPI ASGI 路由、开发 PostgreSQL、智谱 `embedding-3`（1024 维）和百炼 `gte-rerank-v2`，非模型 mock。合成文档包含长短句、三个标题区和一个 700 字符长元素：上传返回 201，实际存储 7 块，每块不超过 300 字符且完整属于一个标题区；序号连续、向量维度正确。检索返回 200，早茶订单对应的餐饮区域排名第一，候选均有真实 rerank 分数。删除返回 200、删除 7 块；临时账号清理完成。Embedding/rerank 均没有回退告警。

本地公共知识库只扫描未重建，16 篇资料在新规则下产生 106 块，长度 10～110 字符。报告见 [CHUNKING_LIVE_2026-10-11_FINAL.json](CHUNKING_LIVE_2026-10-11_FINAL.json)。已有入库资料需要重新切分/生成向量，更新方式和旧索引的清理注意点见 [CHUNKING.md](CHUNKING.md)。
