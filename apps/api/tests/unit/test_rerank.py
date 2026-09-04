"""Cross-encoder reranking, including what happens when it is not installed."""

from __future__ import annotations

from typing import Any

import pytest

from zk2.config import get_settings
from zk2.retrieval import rerank as rerank_module
from zk2.retrieval.base import RetrievedChunk
from zk2.retrieval.rerank import rerank, reset_model_cache

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_model_cache() -> None:
    reset_model_cache()


def chunk(chunk_id: int, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=1,
        source_name="doc.txt",
        ordinal=chunk_id,
        text=text,
        score=1.0 / chunk_id,
        retriever="rrf",
        matched_by=("dense", "bm25"),
    )


CANDIDATES = [chunk(1, "unrelated filler"), chunk(2, "the answer"), chunk(3, "more filler")]


class FakeCrossEncoder:
    """Scores by keyword presence - enough to prove the ordering is applied."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.seen: list[tuple[str, str]] = []

    def predict(self, pairs: list[tuple[str, str]], **_kwargs: Any) -> list[float]:
        self.seen = pairs
        return [1.0 if query.lower() in passage.lower() else 0.1 for query, passage in pairs]


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> FakeCrossEncoder:
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", True)
    model = FakeCrossEncoder()
    monkeypatch.setattr(rerank_module, "_load_model", lambda: model)
    return model


async def test_disabled_by_default_keeps_the_fused_order() -> None:
    result = await rerank("the answer", CANDIDATES)
    assert [c.chunk_id for c in result] == [1, 2, 3]


async def test_reranking_promotes_the_relevant_chunk(enabled: FakeCrossEncoder) -> None:
    result = await rerank("the answer", CANDIDATES)
    assert result[0].chunk_id == 2
    assert result[0].retriever == "rerank"
    assert result[0].matched_by == ("dense", "bm25")


async def test_top_k_truncates_after_reranking(enabled: FakeCrossEncoder) -> None:
    result = await rerank("the answer", CANDIDATES, top_k=1)
    assert [c.chunk_id for c in result] == [2]


async def test_query_and_passage_are_scored_together(enabled: FakeCrossEncoder) -> None:
    await rerank("the answer", CANDIDATES)
    assert enabled.seen == [("the answer", c.text) for c in CANDIDATES]


async def test_a_single_candidate_is_not_worth_a_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", True)

    def _boom() -> None:
        raise AssertionError("the model must not be loaded for one candidate")

    monkeypatch.setattr(rerank_module, "_load_model", _boom)
    assert await rerank("q", CANDIDATES[:1]) == CANDIDATES[:1]


async def test_missing_extra_degrades_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No sentence-transformers installed: answer quality drops, chat still works."""
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", True)
    monkeypatch.setattr(rerank_module, "_load_model", lambda: None)
    result = await rerank("the answer", CANDIDATES, top_k=2)
    assert [c.chunk_id for c in result] == [1, 2]


async def test_a_model_that_raises_does_not_break_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", True)

    class BrokenModel:
        def predict(self, pairs: list[tuple[str, str]], **_kwargs: Any) -> list[float]:
            raise RuntimeError("CUDA out of memory")

    broken = BrokenModel()
    monkeypatch.setattr(rerank_module, "_load_model", lambda: broken)
    result = await rerank("the answer", CANDIDATES)
    assert [c.chunk_id for c in result] == [1, 2, 3]


def test_load_failure_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The import is attempted once, not on every chat turn."""
    calls = {"n": 0}

    def _import_fails(*_args: object, **_kwargs: object) -> None:
        calls["n"] += 1
        raise ImportError("no sentence_transformers")

    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", True)
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)
    monkeypatch.setattr(rerank_module, "CrossEncoderLike", object)
    rerank_module._load_model()
    rerank_module._load_model()
    assert rerank_module._load_failed is True
