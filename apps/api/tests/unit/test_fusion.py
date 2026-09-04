"""Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest

from zk2.retrieval.base import RetrievedChunk
from zk2.retrieval.fusion import reciprocal_rank_fusion

pytestmark = pytest.mark.unit


def chunk(chunk_id: int, retriever: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=1,
        source_name="doc.txt",
        ordinal=chunk_id,
        text=f"chunk {chunk_id}",
        score=score,
        retriever=retriever,
    )


def test_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_single_list_keeps_its_order() -> None:
    ranked = [chunk(i, "dense") for i in (7, 3, 9)]
    fused = reciprocal_rank_fusion([ranked])
    assert [c.chunk_id for c in fused] == [7, 3, 9]


def test_agreement_beats_a_single_strong_hit() -> None:
    """Rank 2 in both lists outranks rank 1 in only one."""
    dense = [chunk(1, "dense"), chunk(2, "dense")]
    lexical = [chunk(3, "bm25"), chunk(2, "bm25")]
    fused = reciprocal_rank_fusion([dense, lexical])
    assert fused[0].chunk_id == 2
    assert set(fused[0].matched_by) == {"dense", "bm25"}


def test_scores_are_rank_based_not_value_based() -> None:
    """A huge raw score cannot buy a better position - only rank counts."""
    dense = [chunk(1, "dense", score=0.51)]
    lexical = [chunk(2, "bm25", score=9999.0)]
    fused = reciprocal_rank_fusion([dense, lexical])
    assert {c.score for c in fused} == {1.0 / 61}


def test_limit_truncates_after_fusion() -> None:
    dense = [chunk(i, "dense") for i in range(10)]
    lexical = [chunk(i, "bm25") for i in range(5, 15)]
    fused = reciprocal_rank_fusion([dense, lexical], limit=3)
    assert len(fused) == 3


def test_ordering_is_stable_for_ties() -> None:
    a = reciprocal_rank_fusion([[chunk(5, "dense"), chunk(4, "dense")]], k=0)
    b = reciprocal_rank_fusion([[chunk(5, "dense"), chunk(4, "dense")]], k=0)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


def test_matched_by_records_every_retriever_once() -> None:
    fused = reciprocal_rank_fusion([[chunk(1, "dense")], [chunk(1, "bm25")], [chunk(1, "bm25")]])
    assert fused[0].matched_by == ("dense", "bm25")
