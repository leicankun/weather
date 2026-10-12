# RAG 工具证据传递

## 问题与修复

此前 `search_my_plans` 和 `search_knowledge` 在检索后又将每个分块裁成前 200 字，默认最多 300 字的分块末尾事实会丢失。模型和多 Agent 核验只看见工具摘要，因此会把“当前未看见”误说成“用户资料没有”，甚至将错误回答判为通过。原始诊断及复测保留在 [2026-10-12 诊断报告](RAG_ERROR_DIAGNOSTIC_2026-10-12.md)。

两种工具现在共用 `backend/app/services/knowledge_tool_context.py`：返回 JSON 文本，保留完整正文和原有换行，以及 `id`、`title`、`source`、`chunk_index`、`rerank_score`、`rrf_score`。不再逐块裁成 200 字或给完整正文附加省略号。工具名称、输入参数和检索 HTTP 接口不变；工具内部成功结果的文本结构改为 JSON，Agent 将它作为 ToolMessage 内容消费。

整体 JSON 输出最多 6000 字符，包括字段、元数据与说明，和核验每条工具证据预算一致。按检索排名保留完整分块前缀；下一个分块放不下即停止，不切开该分块，也不跳过它而优先塞入更低排名资料。默认最多 10 个、每块最多 300 字时，常规元数据可以全部纳入；遗留超长分块、异常长标题等可能导致省略。

输出状态字段：

| 字段 | 含义 |
| --- | --- |
| `scope` | `public` 或 `personal` |
| `retrieved_count` | 本次 Retriever 返回的分块数，不是整个知识库总数 |
| `included_count` | 本次工具实际提供的完整分块数 |
| `omitted_count` | 因整体预算没有纳入的分块数 |
| `context_incomplete` | 是否存在上述预算省略 |
| `notice` | 说明片段范围及省略情况 |
| `items` | 带来源元数据的完整已纳入正文 |

单个分块本身超过整体预算时，返回空 `items`、省略数量和不完整提示，不把半段内容伪装成完整分块。没有检索命中时仍使用原有的空结果说明。单/多 Agent 及核验提示词明确：不完整片段不能证明原资料没有答案，不得猜测上传失败或未索引；资料中的指令不能执行。提示词降低误判风险，但不保证模型永远正确。

多 Agent 执行器还将 MCP 适配器返回的纯文本内容块解包为原文本，而不是 `str(list)`，避免额外转义造成证据预算膨胀。混合或非文本内容保留原先处理方式。核验读取完整已纳入的工具输出；此次没有额外加载全部检索候选或保存一份无预算的独立证据库。其他工具证据及总核验预算继续按原规则限制。

## 回归与真实验收（2026-10-12）

新增 `tests/unit/test_rag_tool_context.py`：先在旧代码复现公共/私人 × 单/多图的 4 个失败，再验证块尾事实进入最终回答和核验、来源元数据与正文换行、整体预算省略、超大分块、十个默认分块，以及 MCP 文本解包不会额外截断证据，共 8 项。图使用实际工具业务函数和真实 LangGraph，检索与模型使用替身；最终答案从实际收到的工具内容生成，不能靠预写正确答案绕过证据丢失。

```powershell
# backend 目录
.\.venv\Scripts\python.exe -m pytest tests/unit/test_rag_tool_context.py tests/unit/test_multi_agent.py tests/unit/test_answer_verification.py --no-db -o addopts= -q
.\.venv\Scripts\python.exe -m app.scripts.evaluate_rag_errors --only tail_context similar_ids latest_notice missing_facts --output ../docs/RAG_FIX_<新名称>.json
.\.venv\Scripts\python.exe -m app.scripts.evaluate_rag_errors --http-only --output ../docs/RAG_FIX_HTTP_<新名称>.json
```

真实脚本使用现有 PostgreSQL、智谱、百炼与 DeepSeek；创建临时合成用户和资料，结束清理，不改 `.env`，已有输出拒绝覆盖。普通诊断直接跑图，不保存对话；`--http-only` 测试真实 ASGI 聊天入口，临时对话、记忆、检查点随测试用户删除。

| 验证 | 结果 |
| --- | --- |
| 首轮相关回归（含比较入口，7 个新用例） | 56 通过 |
| MCP 文本解包补充后相关回归（8 个新用例） | 55 通过，覆盖最后代码变化 |
| 完整 `python -m pytest` | 937 通过、2 项原有通知失败、15 warnings，无跳过；全量会话收集到首批 7 个新用例，随后新增的第 8 项由上述最终相关回归覆盖 |
| 完整图真实复测 | 4 组问题 × 单/多 Agent，共 8 份回答逐份人工核对符合资料；保留 6 次重排超时降级记录，Embedding 无回退告警 |
| 真实聊天 HTTP 入口 | 单/多模式各 1 次，HTTP 200，均正确回答 `16:47`、西门 C3、紫鹭 928；无 Embedding/rerank 降级告警，临时数据清理成功 |
| 真实 stdio MCP 公共检索 | `search_knowledge` 返回 JSON 文本内容块，含来源/分块编号；第一块 281 字完整保留（超过旧 200 字限制），3 条结果无预算省略；两模型请求均 HTTP 200 |
| `ruff check .` | 通过 |
| `ruff format --check .` | 237 文件已格式化；唯一失败是任务前 `embedding_client.py` 行尾空格，保留用户编辑 |
| 前端/浏览器 | 本次未修改前端，未重复相关检查 |

完整后端的已知失败为 `TestVapidKey::test_返回公钥`（本地未配置 VAPID 公钥）与 `TestPrefs::test_默认全开`（断言遗漏既有 `risk_enabled` 字段），与 RAG 改动无关。不将它们隐藏为全量通过。

真实回答存在空格形式，如“西门 C3”“紫鹭 928”；脚本的原始字符串 marker 检查可能显示 false。最终结论基于逐份阅读，与资料语义一致，不以 marker 或模型核验状态替代质量评估。

原始结果：[完整图复测](RAG_FIX_VALIDATION_2026-10-12.json)、[真实聊天入口](RAG_FIX_HTTP_2026-10-12.json)、[真实 MCP 公共检索](RAG_MCP_FIX_2026-10-12.json)。诊断期发现的低相关候选返回和 Embedding 伪向量混用风险仍存在，不属于本次证据传递修复范围。
