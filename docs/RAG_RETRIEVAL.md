# 知识库混合检索

公共知识库与个人知识库都使用：**BM25 召回 + pgvector 向量召回 → RRF 融合排名 → gte-rerank-v2 重排序 → 最终 Top-K**。历史聊天记录没有接入此检索。

## 两路召回

- BM25：使用 [jieba 搜索模式分词](https://github.com/fxsjy/jieba)，统一全角/半角和英文大小写，对标题与正文统计词频、文档频率和长度。评分包含词频饱和、逆文档频率及长度归一化，使用 [Lucene 文档中的正 IDF 形式](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)：`log(1 + (N - df + 0.5) / (df + 0.5))`。零关键词匹配的片段不进入 BM25 候选；没有向量的片段也能被召回。
- Dense：沿用智谱 Embedding / 本地哈希向量兜底，使用 PostgreSQL + pgvector 的余弦距离排序。没有向量的片段不进入这一路；查询零向量时跳过 Dense，非有限余弦分数也不会进入融合。
- 两路独立取候选，默认每路 30 条；候选数至少等于最终 Top-K。个人库的语料读取、BM25 文档频率统计和 Dense 查询都限定同一个 `user_id`，公共库与个人库分别检索。

## RRF 融合

片段按数据库主键去重，每条召回列表的排名从 1 开始：

```text
rrf_score(document) = Σ 1 / (RAG_RRF_K + rank(document))
```

只在一路出现就贡献一次，两路都出现就累加两次，不直接相加 BM25 和余弦相似度。默认 `RAG_RRF_K=60`，两路均排名第一时为 `2 / 61 ≈ 0.03278689`。按融合分数降序排序；同分时优先 BM25 排名，再按主键稳定排序。随后截取重排候选池，模型排序完成后才取最终结果，返回条数为 1～10，默认 5。

## 模型重排序

客户端 `app/services/rerank_client.py` 使用已有 `httpx` 调用[百炼文本排序接口](https://help.aliyun.com/zh/model-studio/text-rerank-api)，模型默认 `gte-rerank-v2`。它不生成回答，而是将原问题与每个候选的“标题 + 正文”配对评分。RRF 前 `max(top_k, RERANK_CANDIDATE_K)` 条进入模型，默认上限 30 条，避免只在已经截断的最终 5 条内排序。发送 `input.query`、`input.documents` 和 `parameters.top_n`，后者取候选总数，`return_documents=false`。

通过响应 `output.results[].index` 映射回本地片段，仅使用 `relevance_score` 排序，保留片段正文、BM25、向量和 RRF 元数据。按原始模型分数降序，同分保持 RRF 顺序。模型分数为 0～1，但主要用于本次候选之间比较，不应作为跨请求的绝对质量标准。最终模型顺序与 RRF 分数顺序可能不同；模型分数不与 RRF 分数相加。

空候选不调用模型。`RERANK_ENABLED=false`、没有百炼 Key、HTTP 错误、超时或返回不完整/重复/越界索引及非法分数时，整批保留 RRF 结果，`rerank_score` 和 `rerank_rank` 为 null；不会用假重排分数填充。异常日志仅记录错误类型或 HTTP 状态，不打印密钥、查询或私有资料。私有候选仍先按用户隔离，公共资料及其他用户资料不会混入该用户的排序请求。

## 返回字段

公共 `GET/POST /api/v1/knowledge/search`、个人 `POST /api/v1/my-knowledge/search` 的响应仍为 `{code,message,data}`，`data.items` 保留标题、来源、正文、分块序号，并返回：

| 字段 | 含义 |
| --- | --- |
| `similarity` | 原有余弦相似度；片段未进入 Dense 候选时为 null |
| `bm25_score` | BM25 原始分数；未进入 BM25 候选时为 0 |
| `rrf_score` | RRF 阶段的融合分数；不是概率或百分比 |
| `dense_rank` | Dense 候选中的排名，未召回为 null |
| `bm25_rank` | BM25 候选中的排名，未召回为 null |
| `rerank_score` | 百炼模型相关性分数；未启用或失败时为 null |
| `rerank_rank` | 重排候选池中的最终模型排名，从 1 开始；未启用或失败时为 null |

个人知识库页面优先显示 `rerank_score`，为空时显示 `rrf_score`；公共 MCP 检索工具和个人本地检索工具同样展示当前排序阶段的分数。因此单/多智能体均通过原有工具调用获得重排结果。

## 配置与运行

更新依赖并重启后端；运行着的 Celery 和独立 MCP 服务也需要重启。无需新增表或数据库扩展。BM25/RRF 不依赖新密钥，启用真实重排需要百炼 API Key，不能复用智谱 Key。已有知识库数据可直接参与混合检索和重排；若之前使用本地哈希兜底向量，改用智谱真实 Embedding 后需重新生成向量。公共库如果原本为空，仍需通过已有 `/api/v1/knowledge/index` 接口建立文档索引。

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

可在 `backend/.env` 按需加入，缺省值已生效：

```dotenv
RAG_CANDIDATE_K=30
RAG_RRF_K=60
RAG_BM25_K1=1.2
RAG_BM25_B=0.75
DASHSCOPE_API_KEY=你的百炼密钥
RERANK_ENABLED=true
RERANK_MODEL=gte-rerank-v2
RERANK_BASE_URL=https://dashscope.aliyuncs.com/api/v1
RERANK_CANDIDATE_K=30
RERANK_TIMEOUT=15
```

优先读取 `DASHSCOPE_API_KEY`，为空时复用已配置的 `QWEN_API_KEY`。默认基址保留 DashScope 原生地址；对于工作空间专属 Key，按官方文档配置 `RERANK_BASE_URL=https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1`，其他地域使用对应地址。这里填 API 基址，客户端会追加 `/services/rerank/text-rerank/text-rerank`，不要填 Qwen 的 OpenAI 兼容基址 `/compatible-mode/v1`。

百炼接口对文档数量和 Token 数有上限，长查询乘以多个候选会增加总输入；当前知识块通常为 300 字，不另行截断查询或正文。超限会按接口错误回退，必要时降低 `RERANK_CANDIDATE_K` 或拆分长资料。默认超时 15 秒，无自动重试；可以配置 0～60 秒之间的正值。模型调用增加网络延迟和百炼用量，关闭开关可立即恢复 RRF 流程。重排采用在线 API，不下载本地模型，不需要本地 GPU。

实现见 `app/services/hybrid_retrieval.py`。当前 BM25 会读取当前范围全部文本，在工作线程中评分；分词结果用有界 LRU 缓存复用，没有单独维护可过期的文档索引，提交新增/修改/删除后下次检索即可反映变化。该方案适合当前较小的知识库；大规模语料下全量文本读取与评分成本随文档数增加，应迁移为持久倒排索引或专用检索服务。

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/test_hybrid_retrieval.py tests/unit/test_rerank_client.py tests/api/test_hybrid_knowledge.py tests/api/test_rerank_knowledge.py tests/api/test_migrations.py -o addopts= -q
```

覆盖 BM25 手算分数、词频饱和和长度惩罚、中文分词、RRF 排名融合与去重、两路互补、无向量与零向量、编号检索、删除后结果变化、公共/个人/不同用户隔离，以及现有迁移回归。重排用例额外验证原生 HTTP 协议、模型 index 映射、Top-K 之前重排、同分/零分、超时/鉴权/限流/错误响应与非法结果整批降级，以及发往模型的候选隔离。API 用例使用真实 PostgreSQL，Embedding 和排序模型使用替身；全局夹具默认关闭外部重排，新用例显式打开并拦截 HTTP，避免开发 Key 影响测试。真实接口验证及全套检查结果见 [VALIDATION.md](VALIDATION.md)。
