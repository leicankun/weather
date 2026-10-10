"""RAG 知识库模块 Schema。"""

from pydantic import BaseModel, Field


class IndexResult(BaseModel):
    documents: int
    chunks: int
    stored: int
    total_in_db: int
    embedding_mode: str


class SearchHit(BaseModel):
    title: str
    source: str | None
    content: str
    chunk_index: int
    similarity: float | None  # 仅 BM25 召回的片段可能没有向量相似度
    bm25_score: float
    rrf_score: float
    dense_rank: int | None
    bm25_rank: int | None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=10)
