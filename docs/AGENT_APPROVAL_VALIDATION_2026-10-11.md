# 2026-10-11 Agent 人工审批验证

单 Agent、多 Agent 共用后端敏感操作管理分支，接入原生 `interrupt()` / `Command` 和现有 PostgreSQL Checkpointer。首批支持删除本人指定旧会话和清空本人长期卡片/历史 RAG；不开放整库删除或任意 SQL。本地 `.env` 与任务前的 `embedding_client.py` 编辑保持不变。

## 回归和页面

| 检查 | 实际结果 |
| --- | --- |
| 审批 + Checkpointer 数据库专项 | 独立专项 40 项通过、1 warning、39.61 秒；最终增加执行期间并发水位用例后，完整套件中 22 项审批 + 19 项 Checkpointer 共 41 项全部通过，无跳过 |
| 最终完整 `python -m pytest` | 930 项通过、2 项原有通知失败、15 warnings，无跳过，253.91 秒；覆盖率 65% |
| `python -m ruff check .` | 通过 |
| `python -m ruff format --check .` | 234 文件通过；只有任务前的 `embedding_client.py:81` 行尾空格未通过，该编辑保留 |
| 前端 `npm run check:js`、`npm --prefix frontend-user run lint` | 通过 |
| 前端 `npm test` | 5 文件、18 项通过 |
| `npm run build` | 管理端和用户端均通过；保留构建产物体积提示 |
| `PLAYWRIGHT_CHANNEL=chrome` + `npm run test:e2e` | 11 项通过、22.3 秒，包括新增批准/拒绝、刷新找回、提交 multi 模式及不提前发决定的两项测试 |
| 修改的 Vue/API/e2e 文件 Prettier 检查 | 通过 |
| 页面视觉检查 | 查看 Chrome 截图 `frontend-user/test-results/approval-true.png`、`approval-false.png`：对象、范围、有效期和批准/拒绝按钮清楚可见，等待期间输入禁用 |
| 开发库升级 | 已应用 `0019_agent_approvals`；此前 `0018_agent_checkpoints` 和已有业务数据保留 |

前端浏览器测试采用 mock API 契约；不是浏览器接真实模型的全栈测试。后端专项使用真实 PostgreSQL 和完整图，模型提案采用替身，未跳过数据库集成测试。

全量仍失败的两项此前已记录：`TestVapidKey::test_返回公钥`（本地公钥为空）和 `TestPrefs::test_默认全开`（预期未包含既有 `risk_enabled` 字段）。本次未修改通知模块，也没有把完整套件表述为全部通过。

专项验证未批准时无数据副作用、普通恢复仍等待、拒绝即取消、两种模式和普通/SSE 行为一致、刷新能找到待审批会话。安全与恢复用例另覆盖他人不能批准/删除、附加参数和非布尔决定被拒绝、过期批准被拒绝、目标变化不删除、清空水位覆盖范围变化不遗忘新消息、目标忙时拒绝仍成功、并发决定只有同一结果、清除检查点取消审批。

注入两类崩溃：一是决定落库后、`Command` 前崩溃，恢复读取原决定；二是删除事务已提交、图节点检查点写入前崩溃，恢复读取回执，不重复删除后来新建的同 ID 会话，即使它正在使用。批量删除会话 + 清空记忆中途异常时全部回滚，再提交同一决定成功；相交记忆的清空回执使用数据库实际删除数量，避免重复计数。最终另增加执行瞬间其他会话写入原始消息的并发用例，验证遗忘水位不扩大到审批之后的新消息。

## 真实模型与 API

运行 `python -m app.scripts.verify_agent_approval`，使用当前配置的真实 DeepSeek、开发 PostgreSQL 和 FastAPI ASGI 接口，模型提案未替换。初次报告 [AGENT_APPROVAL_LIVE_2026-10-11.json](AGENT_APPROVAL_LIVE_2026-10-11.json) 和补充审批等待边界后的 [中间复测](AGENT_APPROVAL_LIVE_2026-10-11_FINAL.json) 保留；最终执行水位冻结与实际数量回执版本再次运行到 [AGENT_APPROVAL_LIVE_2026-10-11_WATERMARK.json](AGENT_APPROVAL_LIVE_2026-10-11_WATERMARK.json)。三份报告各自都是 6 个场景、6 项断言通过；最终运行退出码 0，开始于 15:18:40、结束于 15:19:07（北京时间；JSON 保存 UTC）。

每种模式执行三次真实请求，共 6 个场景、6 项结果断言：

1. 请求删除明确的合成旧会话，暂停后目标仍有两条消息，通用 resume 仍等待；拒绝后原数据保留，重复拒绝不重复保存。
2. 再次请求同一目标，批准后才删除；重复批准保持相同结果，没有重复副作用。
3. 手动种入带来源的合成卡片/历史片段，模型提出清空长期记忆；批准后两层记忆清空、原始聊天保留，服务端记录执行回执。

这次真实测试只验证敏感管理图，不联调天气/路线等专业工具；Embedding/reranker 无需调用，卡片和历史片段是合成种子。用户批准/拒绝由验收脚本发起认证 API 请求，真实页面交互由上面的浏览器契约测试单独验证。通过数依据数据库状态与 API 断言，不依赖模型宣称“完成”。

三份报告均通过已配置 API Key/JWT secret 原文扫描。开发库复查临时 `approval_live_*` 用户为 0、三批报告中的审批审计为 0，测试账号级联清理完成。只创建本地提交，不推送上游。
