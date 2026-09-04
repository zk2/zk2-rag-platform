"""Parsing citation markers out of an answer."""

from __future__ import annotations

from typing import Any

import pytest

from zk2.chat.rag import parse_citations

pytestmark = pytest.mark.unit


def context(count: int) -> list[dict[str, Any]]:
    return [
        {
            "marker": i,
            "chunk_id": 100 + i,
            "source_id": 1,
            "name": "doc.txt",
            "ordinal": i,
            "cited": False,
        }
        for i in range(1, count + 1)
    ]


def test_no_citations() -> None:
    assert parse_citations("A plain answer with no markers.", context(3)) == []


def test_single_citation() -> None:
    assert parse_citations("The limit is 20 days [2].", context(3)) == [2]


def test_multiple_citations_keep_first_appearance_order() -> None:
    answer = "First [3]. Then [1]. Again [3]."
    assert parse_citations(answer, context(3)) == [3, 1]


def test_adjacent_markers() -> None:
    assert parse_citations("Both agree [1][2].", context(3)) == [1, 2]


def test_markers_outside_the_supplied_range_are_ignored() -> None:
    """A model that invents [7] out of three passages has cited nothing."""
    assert parse_citations("As shown in [7].", context(3)) == []


def test_empty_context_means_no_citations() -> None:
    assert parse_citations("Everything is [1] cited.", []) == []


def test_bracketed_text_is_not_a_citation() -> None:
    assert parse_citations("See [appendix] and [A1] for details.", context(3)) == []


def test_long_numbers_are_not_citations() -> None:
    """Year-like or id-like brackets must not be read as markers."""
    assert parse_citations("Filed in [2026] under [1234].", context(9)) == []
