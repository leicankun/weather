"""百炼排序协议、索引映射及失败降级，不访问真实模型。"""

import json

import httpx
import pytest

from app.core.config import settings
from app.services.rerank_client import RerankClient

BASE_URL = "https://rerank.test/api/v1"
URL = BASE_URL + "/services/rerank/text-rerank/text-rerank"
CANDIDATES = [
    {"id": 10, "title": "早茶", "content": "餐厅", "rrf_score": 0.03},
    {"id": 20, "title": "室内", "content": "博物馆", "rrf_score": 0.02},
]


@pytest.fixture(autouse=True)
def enable_rerank(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)


def client():
    return RerankClient(api_key="test-secret", base_url=BASE_URL)


async def test_发送原问题和标题正文按输入索引映射且保留检索分数(respx_mock):
    route = respx_mock.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 0, "relevance_score": 0.1, "document": {"text": "替换内容"}},
                        {"index": 1, "relevance_score": 0.9},
                    ]
                }
            },
        )
    )
    results = await client().rerank("下雨去哪", CANDIDATES)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert json.loads(request.content) == {
        "model": "gte-rerank-v2",
        "input": {"query": "下雨去哪", "documents": ["早茶\n餐厅", "室内\n博物馆"]},
        "parameters": {"top_n": 2, "return_documents": False},
    }
    assert [item["id"] for item in results] == [20, 10]
    assert [item["rerank_rank"] for item in results] == [1, 2]
    assert results[0]["rerank_score"] == 0.9
    assert results[0]["rrf_score"] == 0.02 and results[1]["content"] == "餐厅"
    assert "rerank_score" not in CANDIDATES[0]


async def test_同分保持融合顺序且零分有效(respx_mock):
    respx_mock.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0},
                        {"index": 0, "relevance_score": 0},
                    ]
                }
            },
        )
    )
    results = await client().rerank("问题", CANDIDATES)
    assert [item["id"] for item in results] == [10, 20]
    assert all(item["rerank_score"] == 0 for item in results)


@pytest.mark.parametrize("mode", ["no_key", "placeholder", "disabled", "empty"])
async def test_不可用时不发请求(mode, monkeypatch, respx_mock):
    c = client()
    candidates = CANDIDATES
    if mode == "no_key":
        c.api_key = ""
    elif mode == "placeholder":
        c.api_key = "sk-xxxxxxxx-test"
    elif mode == "disabled":
        monkeypatch.setattr(settings, "RERANK_ENABLED", False)
    else:
        candidates = []
    results = await c.rerank("问题", candidates)
    assert [item["id"] for item in results] == [item["id"] for item in candidates]
    assert all(item["rerank_score"] is None and item["rerank_rank"] is None for item in results)
    assert not respx_mock.calls


def test_优先独立百炼密钥其次复用通义配置(monkeypatch):
    monkeypatch.setattr(settings, "QWEN_API_KEY", "qwen-secret")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "dashscope-secret")
    assert RerankClient().api_key == "dashscope-secret"
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    assert RerankClient().api_key == "qwen-secret"
    assert RerankClient(api_key="").api_key == ""


@pytest.mark.parametrize("failure", [401, 429, 503, "timeout", "connect", "json"])
async def test_接口错误保留完整RRF顺序且日志不泄露密钥(failure, respx_mock, caplog):
    route = respx_mock.post(URL)
    if isinstance(failure, int):
        route.mock(return_value=httpx.Response(failure, json={"message": "test-secret"}))
    elif failure == "json":
        route.mock(return_value=httpx.Response(200, text="not JSON test-secret"))
    else:
        error = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
        route.mock(side_effect=error("test-secret"))
    results = await client().rerank("问题", CANDIDATES)
    assert [item["id"] for item in results] == [10, 20]
    assert all(item["rerank_score"] is None for item in results)
    assert "test-secret" not in caplog.text and "保留 RRF" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        {"code": "InvalidApiKey"},
        {"output": {"results": []}},
        {"output": {"results": [{"index": 0, "relevance_score": 0.5}]}},
        {"output": {"results": "invalid"}},
        None,
        *[
            {"output": {"results": [{"index": 0, "relevance_score": 0.5}, bad]}}
            for bad in (
                {"index": 0, "relevance_score": 0.8},
                {"index": 2, "relevance_score": 0.8},
                {"index": -1, "relevance_score": 0.8},
                {"index": True, "relevance_score": 0.8},
                {"index": "1", "relevance_score": 0.8},
                {"index": 1, "relevance_score": "0.8"},
                {"index": 1, "relevance_score": True},
                {"index": 1, "relevance_score": -0.1},
                {"index": 1, "relevance_score": 1.1},
                {"index": 1, "relevance_score": None},
                {"index": 1},
                None,
            )
        ],
    ],
)
async def test_不完整重复越界或异常评分整批降级(body, respx_mock):
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json=body))
    results = await client().rerank("问题", CANDIDATES)
    assert [item["id"] for item in results] == [10, 20]
    assert all(item["rerank_score"] is None for item in results)


@pytest.mark.parametrize("score", [float("nan"), float("inf")])
async def test_非有限分数降级(score, respx_mock):
    body = {
        "output": {
            "results": [
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": score},
            ]
        }
    }
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text=json.dumps(body)))
    assert all(item["rerank_score"] is None for item in await client().rerank("问题", CANDIDATES))
