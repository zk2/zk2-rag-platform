"""OpenAI embedding adapter: batching and dimension normalisation (ADR-0004)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from zk2.config import get_settings
from zk2.core.errors import ValidationError
from zk2.llm.openai_provider import OpenAIEmbeddings

pytestmark = pytest.mark.unit


class FakeEmbeddingsAPI:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        width = kwargs.get("dimensions", 1536)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * width) for _ in kwargs["input"]]
        )


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeEmbeddingsAPI:
    api = FakeEmbeddingsAPI()
    monkeypatch.setattr(
        "zk2.llm.openai_provider.AsyncOpenAI",
        lambda **_kwargs: SimpleNamespace(embeddings=api),
    )
    return api


async def test_requests_the_configured_width(fake_api: FakeEmbeddingsAPI) -> None:
    provider = OpenAIEmbeddings(api_key="sk-test", model="text-embedding-3-large")
    vectors = await provider.embed_documents(["one", "two"])

    assert fake_api.calls[0]["dimensions"] == get_settings().ingest.embedding_dimensions
    assert len(vectors[0]) == 1536, "3072-wide vectors have no HNSW index"


async def test_batches_large_documents(
    fake_api: FakeEmbeddingsAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().ingest, "embedding_batch_size", 100)
    provider = OpenAIEmbeddings(api_key="sk-test")
    vectors = await provider.embed_documents([f"chunk {i}" for i in range(250)])

    assert len(fake_api.calls) == 3, "a 250-chunk document must not go out as one request"
    assert [len(c["input"]) for c in fake_api.calls] == [100, 100, 50]
    assert len(vectors) == 250


async def test_empty_input_makes_no_request(fake_api: FakeEmbeddingsAPI) -> None:
    provider = OpenAIEmbeddings(api_key="sk-test")
    assert await provider.embed_documents([]) == []
    assert fake_api.calls == []


async def test_query_embedding_is_a_single_vector(fake_api: FakeEmbeddingsAPI) -> None:
    provider = OpenAIEmbeddings(api_key="sk-test")
    vector = await provider.embed_query("question")
    assert len(vector) == 1536
    assert len(fake_api.calls) == 1


def test_model_that_cannot_truncate_is_rejected(
    fake_api: FakeEmbeddingsAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed-width model that does not match the configured width must fail loudly."""
    monkeypatch.setitem(
        __import__("zk2.llm.openai_provider", fromlist=["_EMB_NATIVE_DIM"])._EMB_NATIVE_DIM,
        "legacy-model",
        3072,
    )
    with pytest.raises(ValidationError, match="does not support"):
        OpenAIEmbeddings(api_key="sk-test", model="legacy-model")
