# 知识库混合检索

公共知识库与个人知识库都使用：**BM25 召回 + pgvector 向量召回 → RRF 融合排名 → 最终 Top-K**。本次按用户要求暂不实现 Reranker；历史聊天记录没有接入此检索。

## 两路召回

- BM25：使用 [jieba 搜索模式分词](https://github.com/fxsjy/jieba)，统一全角/半角和英文大小写，对标题与正文统计词频、文档频率和长度。评分包含词频饱和、逆文档频率及长度归一化，使用 [Lucene 文档中的正 IDF 形式](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)：`log(1 + (N - df + 0.5) / (df + 0.5))`。零关键词匹配的片段不进入 BM25 候选；没有向量的片段也能被召回。
- Dense：沿用智谱 Embedding / 本地哈希向量兜底，使用 PostgreSQL + pgvector 的余弦距离排序。没有向量的片段不进入这一路；查询零向量时跳过 Dense，非有限余弦分数也不会进入融合。
- 两路独立取候选，默认每路 30 条；候选数至少等于最终 Top-K。个人库的语料读取、BM25 文档频率统计和 Dense 查询都限定同一个 `user_id`，公共库与个人库分别检索。

## RRF 融合

片段按数据库主键去重，每条召回列表的排名从 1 开始：

```text
rrf_score(document) = Σ 1 / (RAG_RRF_K + rank(document))
```

只在一路出现就贡献一次，两路都出现就累加两次，不直接相加 BM25 和余弦相似度。默认 `RAG_RRF_K=60`，两路均排名第一时为 `2 / 61 ≈ 0.03278689`。按融合分数降序排序；同分时优先 BM25 排名，再按主键稳定排序。融合完成后才取最终结果，返回条数为 1～10，默认 5。

## 返回字段

公共 `GET/POST /api/v1/knowledge/search`、个人 `POST /api/v1/my-knowledge/search` 的响应仍为 `{code,message,data}`，`data.items` 保留标题、来源、正文、分块序号，并返回：

| 字段 | 含义 |
| --- | --- |
| `similarity` | 原有余弦相似度；片段未进入 Dense 候选时为 null |
| `bm25_score` | BM25 原始分数；未进入 BM25 候选时为 0 |
| `rrf_score` | 最终排序使用的融合分数；不是概率或百分比 |
| `dense_rank` | Dense 候选中的排名，未召回为 null |
| `bm25_rank` | BM25 候选中的排名，未召回为 null |

个人知识库页面优先显示 `rrf_score`；公共 MCP 检索工具和个人本地检索工具也按融合排名输出结果，因此单/多智能体无需改变调用方式。

## 配置与运行

更新依赖并重启后端；运行着的 Celery 和独立 MCP 服务也需要重启。无需新增表、数据库扩展或模型密钥，已有知识库数据直接参与混合检索，无需为 BM25 重新生成向量。公共库如果原本为空，仍需通过已有 `/api/v1/knowledge/index` 接口建立文档索引。

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
```

实现见 `app/services/hybrid_retrieval.py`。当前 BM25 会读取当前范围全部文本，在工作线程中评分；分词结果用有界 LRU 缓存复用，没有单独维护可过期的文档索引，提交新增/修改/删除后下次检索即可反映变化。该方案适合当前较小的知识库；大规模语料下全量文本读取与评分成本随文档数增加，应迁移为持久倒排索引或专用检索服务。

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/test_hybrid_retrieval.py tests/api/test_hybrid_knowledge.py tests/api/test_migrations.py -o addopts= -q
```

覆盖 BM25 手算分数、词频饱和和长度惩罚、中文分词、RRF 排名融合与去重、两路互补、无向量与零向量、编号检索、删除后结果变化、公共/个人/不同用户隔离，以及现有迁移回归。API 用例使用真实 PostgreSQL，Embedding 使用替身；真实接口验证及全套检查结果见 [VALIDATION.md](VALIDATION.md)。
