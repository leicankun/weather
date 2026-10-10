"""验证 BM25 数学性质、中文分词及 RRF 按排名融合。"""

import math

import pytest

from app.services.hybrid_retrieval import bm25_recall, reciprocal_rank_fusion, tokenize


def document(key, content):
    return {"id": key, "title": "", "content": content, "source": "test", "chunk_index": 0}


def test_中文无空格与英文编号分词():
    tokens = tokenize("广州塔门票 GZ2026-ABC，ＴＩＣＫＥＴ")
    assert "广州" in tokens and "门票" in tokens
    assert "gz2026" in tokens and "abc" in tokens and "ticket" in tokens
    assert not {"，", "-", " "}.intersection(tokens)


def test_bm25_匹配手算词频及长度归一化():
    rows = [document(1, "alpha alpha"), document(2, "beta")]
    results = bm25_recall("alpha", rows, 5, k1=1.2, b=0.75)
    expected = math.log(2) * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 2 / 1.5))
    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["bm25_score"] == pytest.approx(expected)


def test_bm25_单片段也能召回常见词():
    result = bm25_recall("广州塔", [document(1, "广州塔门票")], 5, k1=1.2, b=0.75)
    assert result[0]["bm25_score"] > 0


def test_bm25_词频饱和与长度惩罚():
    rows = [document(1, "alpha"), document(2, "alpha " * 10)]
    scores = bm25_recall("alpha", rows, 5, k1=1.2, b=0)
    assert scores[0]["id"] == 2
    assert scores[0]["bm25_score"] < scores[1]["bm25_score"] * 10
    rows = [document(1, "alpha"), document(2, "alpha " + "beta " * 20)]
    assert bm25_recall("alpha", rows, 5, k1=1.2, b=0.75)[0]["id"] == 1


@pytest.mark.parametrize(
    "query, rows",
    [
        ("alpha", []),
        ("我的吗", [document(1, "beta")]),
        ("alpha", [document(1, "beta")]),
        ("alpha", [document(1, "")]),
    ],
)
def test_bm25_空语料或无匹配不召回(query, rows):
    assert bm25_recall(query, rows, 5, k1=1.2, b=0.75) == []


def test_rrf_按排名融合而不是直接加不同量纲分数():
    dense = [{**document(1, "a"), "similarity": 0.999}, {**document(2, "b"), "similarity": 0.1}]
    sparse = [{**document(3, "c"), "bm25_score": 1000}, {**document(2, "b"), "bm25_score": 0.01}]
    results = reciprocal_rank_fusion(dense, sparse, k=60)
    assert [item["id"] for item in results] == [2, 3, 1]
    assert results[0]["rrf_score"] == pytest.approx(2 / 62)
    assert results[0]["similarity"] == 0.1
    assert results[0]["bm25_score"] == 0.01
    assert results[0]["dense_rank"] == results[0]["bm25_rank"] == 2
    assert results[1]["similarity"] is None
    assert "rrf_score" not in dense[0]  # 不修改输入列表/共享消息


def test_rrf_重复片段只贡献一次排名且空路有效():
    hit = {**document(1, "alpha"), "similarity": 1.0}
    other = {**document(2, "beta"), "similarity": 0.5}
    results = reciprocal_rank_fusion([hit, hit, other], [], k=60)
    assert len(results) == 2
    assert results[0]["rrf_score"] == pytest.approx(1 / 61)
    assert results[1]["dense_rank"] == 2
    assert reciprocal_rank_fusion([], [], k=60) == []


def test_rrf_拒绝无效常数():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], [], k=0)
