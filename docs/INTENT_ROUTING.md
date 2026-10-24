# 向量意图识别

单、多智能体的文字入口统一使用 `app/services/intent_router.py`：示例问句向量匹配、多领域选择、有界上下文继承。原来的强/弱业务关键词、正则领域路由和对话模型分类兜底已移除。工具执行、预算、汇总与证据核验继续由原有图控制。

## 算法

1. 原有删除会话/清空记忆检测优先，进入独占审批分支；相似度不代替授权，旧管理操作也不通过上下文继承再次触发。
2. 当前输入剥离服务端追加的偏好、附件正文等提示词块，最多取前 4000 字符，按标点和通用连接词拆成最多 8 个片段。分句规则不含业务分类关键词。
3. 使用现有智谱 Embedding 为片段生成真实向量，与 `intent_examples.py` 中示例做归一化余弦比较。每个类别取最近两个示例的平均分。
4. 最高分低于阈值时追问；最高为闲聊、追问、取消表达时按对应控制类别处理。最高为业务领域时，选择达到阈值且距最高分不超过多领域分差的业务类别；各片段结果合并、去重、按注册顺序排列。保留穿搭依赖天气的补全规则。
5. 只有当前问题判为省略式追问时，才反向查找本会话最近最多 3 条用户消息，跳过连续追问，继承最近明确业务问题的领域。遇到闲聊、不确定或管理操作就停止，不越过话题切换寻找旧任务。没有用户原文时，可以用最近一份摘要识别前文领域。助手答复不独立作为用户意图。
6. 明确的新问题覆盖旧领域；地点、日期等参数仍交给现有历史、日期上下文和工具参数生成处理。此模块继承领域，不改写原话。

五个业务领域为 `weather/outfit/route/itinerary/knowledge`，另有 `general/followup/ignore` 控制类别，目前共 102 条示例。示例与公共/私人 RAG 语料分开，不新增表、迁移、向量数据库或依赖。

## 接入与缓存

- 单模式保存完整 `domains/routing`，仍由同一个 Agent 使用全部业务工具；多模式按领域分派。旧 `intent` 字段继续映射到原标签词表，响应形状不变。
- 普通/SSE 复用同一决策，SSE 不重复分类。低分/无可继承前文进入确定性追问节点，服务故障进入明确的不可用提示节点，不把空领域误当成闲聊。
- `routing` 包含 `domains/scores/status/inherited`，分数是相似度而非正确率，随 Checkpointer 保存。恢复使用保存的决策，新一轮清空再识别；旧快照若尚未分类则运行当前分类节点。
- 只有示例向量放入进程缓存，缓存键含模型、维度、地址、凭据摘要和示例内容。完全生成并校验后才发布；同一事件循环内并发首请求共用预热，失败不留下半成品，下次可重试。用户问题、历史与结果不放入全局缓存。
- 路由使用 `embed_texts(..., allow_fallback=False)`，缺少 Key、网络/超时或无效响应都明确停止，不使用本地哈希伪向量。原有 RAG 默认降级策略保留。响应新增数量、索引顺序、维度、有限数值和非零向量校验。

## 配置与运行

复用 `ZHIPU_API_KEY/ZHIPU_EMBED_BASE_URL/ZHIPU_EMBED_MODEL/EMBED_DIM`，此次不修改本地 `.env`。可选参数如下：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `INTENT_SIMILARITY_THRESHOLD` | 0.60 | 最近两个示例平均余弦分门槛 |
| `INTENT_MULTI_MARGIN` | 0.015 | 同一片段其他业务领域相对最高分的最大差值 |
| `INTENT_CONTEXT_TURNS` | 3 | 最多回看的用户消息条数 |
| `INTENT_ROUTING_TIMEOUT` | 30 秒 | 含示例预热和前文查询的整个识别上限 |

更新后重启后端即可。每个进程首轮分批生成示例向量，后续只生成当前输入与必要前文向量；首次延迟较高，Embedding 会产生调用费用。修改示例代码需要重启，换模型/示例集需要重新校准。

## 验证与限制

backend 目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_agent_intent.py tests/unit/test_agent_registry.py tests/unit/test_embedding_strict.py tests/unit/test_multi_agent.py --no-db -o addopts= -q
.\.venv\Scripts\python.exe -m app.scripts.evaluate_intent_routing --suite calibration --output ../docs/INTENT_ROUTING_CALIBRATION_<新名称>.json
.\.venv\Scripts\python.exe -m app.scripts.evaluate_intent_routing --suite acceptance --http --output ../docs/INTENT_ROUTING_ACCEPTANCE_<新名称>.json
```

真实评测调用付费 Embedding，`--http` 另调用真实对话模型和匿名普通/SSE 接口；不写开发库、账号或 `.env`，已有报告拒绝覆盖。单元/API 编排回归使用有明确标签的正交向量替身，不能据此证明语义理解能力。

首轮校准 15/23，问题集中在交叉领域示例和过宽分差。调整后同一校准集 22/23，剩余一次为连接故障，原始报告保留。另一组独立验收 25/25，匿名单/多模式普通/SSE 四次实调全部通过，见 [真实验收](INTENT_ROUTING_ACCEPTANCE_2026-10-24.json)。小样本结果不代表线上总体准确率。

向量仍可能混淆否定、隐含多意图、复杂指代和交叉领域。分句、片段上限、历史回看上限和摘要压缩影响召回。单纯继承前文领域无法可靠判断用户是否只追问复合问题中的一部分；明确说出新领域会优先按当前输入路由。应收集真实误判，补充示例，并保留独立验收数据。
