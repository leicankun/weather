"""Strict routing embeddings never silently fall back or reorder query vectors."""

import httpx
import pytest

from app.services.embedding_client import EmbeddingClient


async def test_strict_embeddings_require_key(monkeypatch):
    client = EmbeddingClient(dim=2)
    client.api_key = ""
    with pytest.raises(ValueError, match="configured API key"):
        await client.embed_texts(["天气"], allow_fallback=False)
    assert len((await client.embed_texts(["天气预报"]))[0]) == 2


async def test_embedding_response_indices_restore_input_order(respx_mock):
    respx_mock.post("https://embedding.test/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )
    )
    client = EmbeddingClient(api_key="fixture", base_url="https://embedding.test", dim=2)
    assert await client.embed_texts(["甲", "乙"], allow_fallback=False) == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 1, "embedding": [1.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": [0.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": ["bad", 0.0]}]},
    ],
)
async def test_invalid_embedding_response_is_rejected(respx_mock, payload):
    respx_mock.post("https://embedding.test/embeddings").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = EmbeddingClient(api_key="fixture", base_url="https://embedding.test", dim=2)
    with pytest.raises(ValueError):
        await client.embed_texts(["甲"], allow_fallback=False)


async def test_http_failure_raises_in_strict_mode_but_retains_rag_fallback(respx_mock):
    respx_mock.post("https://embedding.test/embeddings").mock(return_value=httpx.Response(503))
    client = EmbeddingClient(api_key="fixture", base_url="https://embedding.test", dim=2)
    with pytest.raises(httpx.HTTPStatusError):
        await client.embed_texts(["天气预报"], allow_fallback=False)
    assert len((await client.embed_texts(["天气预报"]))[0]) == 2
