"""Cleanup applied to every document before it is chunked."""

from __future__ import annotations

import time

import pytest

from zk2.sources.chunking import count_tokens
from zk2.sources.text_clean import MAX_RUN, clean_text, fold_long_runs, strip_null_bytes

pytestmark = pytest.mark.unit


def test_null_bytes_are_removed() -> None:
    """Postgres refuses them in a text column, so ingest would fail at the last step."""
    assert strip_null_bytes("a\x00b") == "ab"


def test_line_endings_and_spacing_are_normalised() -> None:
    assert clean_text("a\r\nb\n\n\n\nc  d") == "a\nb\n\nc d"


def test_long_runs_are_folded() -> None:
    folded = fold_long_runs("Chunking" + "." * 200 + "12")
    assert "." * MAX_RUN in folded
    assert "." * (MAX_RUN + 1) not in folded


def test_a_table_of_contents_line_loses_its_dot_leader() -> None:
    line = "1. What is RAG" + "." * 120 + "2"
    assert len(clean_text(line)) < len(line) - 80


def test_counting_tokens_of_a_pathological_run_stays_fast() -> None:
    """The BPE is quadratic on repeated characters; one upload must not stall the worker."""
    started = time.perf_counter()
    count_tokens("�" * 20_000)
    assert time.perf_counter() - started < 2.0
