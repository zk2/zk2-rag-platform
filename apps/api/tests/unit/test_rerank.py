"""Cross-encoder reranking, including what happens when it is not installed."""

from __future__ import annotations

from typing import Any

import pytest

from zk2.config import get_settings
from zk2.retrieval import rerank as rerank_module
from zk2.retrieval.base import RetrievedChunk
from zk2.retrieval.rerank import rerank, reset_model_cache, warm_up

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


async def test_turning_it_off_keeps_the_fused_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", False)
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


async def test_only_the_head_of_the_list_is_scored(
    enabled: FakeCrossEncoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoring is linear in candidates and the user is waiting for the answer.

    Measured on twelve CPU cores, the default model takes 2.3s over fifteen
    candidates and 3.5s over thirty - on a turn that otherwise runs in two to
    four seconds. The tail of a fused list pays that price and almost never
    reaches the prompt.
    """
    monkeypatch.setattr(get_settings().retrieval, "rerank_candidates", 2)
    candidates = [chunk(1, "filler"), chunk(2, "filler"), chunk(3, "the answer")]

    result = await rerank("the answer", candidates)

    assert len(enabled.seen) == 2, "only the head was handed to the model"
    # The unscored tail keeps its fused rank rather than disappearing
    assert [c.chunk_id for c in result] == [1, 2, 3]
    assert result[-1].retriever == "rrf"


async def test_the_cap_does_not_shorten_the_result(
    enabled: FakeCrossEncoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().retrieval, "rerank_candidates", 1)
    result = await rerank("the answer", CANDIDATES)
    assert len(result) == len(CANDIDATES)


async def test_warm_up_loads_the_model_before_anyone_asks(
    enabled: FakeCrossEncoder, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(rerank_module, "_load_model", lambda: loaded.append("x"))
    await warm_up()
    assert loaded == ["x"]


async def test_warm_up_does_nothing_when_reranking_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings().retrieval, "rerank_enabled", False)
    monkeypatch.setattr(
        rerank_module, "_load_model", lambda: pytest.fail("loaded a model nobody will use")
    )
    await warm_up()
