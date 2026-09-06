"""Packing parsed segments into chunks."""

from __future__ import annotations

import pytest

from zk2.sources.chunking import (
    DEFAULT_CHUNK_TOKENS,
    ChunkStrategy,
    chunk_document,
    chunk_text,
    count_tokens,
)
from zk2.sources.parsed import ParsedDocument, Segment

pytestmark = pytest.mark.unit


def _doc(*segments: Segment) -> ParsedDocument:
    return ParsedDocument(segments=list(segments))


# --- plain text -------------------------------------------------------


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("The quick brown fox jumps over the lazy dog.")
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].tokens == count_tokens(chunks[0].text)


def test_long_text_splits_with_sequential_ordinals() -> None:
    text = " ".join(f"word{i}" for i in range(3000))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.tokens <= 100 for c in chunks)


def test_chunks_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)
    first_tail = chunks[0].text.split()[-5:]
    assert any(token in chunks[1].text for token in first_tail)


def test_no_overlap_when_disabled() -> None:
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, chunk_tokens=50, overlap_tokens=0)
    # Zero overlap means the pieces together are the source and nothing more
    assert sum(c.tokens for c in chunks) <= count_tokens(text) + len(chunks)


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError, match="chunk_tokens must be positive"):
        chunk_text("text", chunk_tokens=0)
    with pytest.raises(ValueError, match="overlap_tokens must be smaller"):
        chunk_text("text", chunk_tokens=10, overlap_tokens=10)


def test_default_chunk_size_is_respected() -> None:
    text = " ".join(f"word{i}" for i in range(5000))
    chunks = chunk_text(text)
    assert all(c.tokens <= DEFAULT_CHUNK_TOKENS for c in chunks)


def test_long_paragraph_is_split_on_sentence_boundaries() -> None:
    sentences = [f"This is sentence number {i} in a long paragraph." for i in range(60)]
    chunks = chunk_text(" ".join(sentences), chunk_tokens=60, overlap_tokens=0)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.strip().endswith(".")


# --- packing ----------------------------------------------------------

SHORT = [
    Segment(text="Router determines the type of the task.", section=("Pattern", "Router")),
    Segment(text="Executor runs the chosen tool.", section=("Pattern", "Executor")),
    Segment(text="Evaluator checks the final answer.", section=("Pattern", "Evaluator")),
]


def test_small_segments_are_packed_into_one_chunk() -> None:
    """The point of the rewrite: a boundary is not a reason to start a chunk."""
    chunks = chunk_document(_doc(*SHORT), title="notes.pdf", chunk_tokens=400)
    assert len(chunks) == 1
    assert "Router" in chunks[0].text
    assert "Evaluator" in chunks[0].text


def test_a_pack_claims_only_the_heading_path_it_shares() -> None:
    chunk = chunk_document(_doc(*SHORT), title="notes.pdf", chunk_tokens=400)[0]
    assert chunk.section == ("Pattern",)
    assert chunk.text.startswith("notes.pdf > Pattern\n")


def test_packing_stops_at_the_budget() -> None:
    segments = [Segment(text=" ".join(f"word{i}" for i in range(80))) for _ in range(10)]
    chunks = chunk_document(_doc(*segments), chunk_tokens=200)
    assert len(chunks) > 1
    assert all(c.tokens <= 200 for c in chunks)


def test_a_full_section_keeps_its_own_heading() -> None:
    """Once a chunk is worth its own heading, the heading is worth the room."""
    long_body = " ".join(f"word{i}" for i in range(300))
    chunks = chunk_document(
        _doc(
            Segment(text=long_body, section=("Vacation",)),
            Segment(text="Receipts within 30 days.", section=("Expenses",)),
        ),
        title="handbook.md",
        chunk_tokens=400,
    )
    assert chunks[0].section == ("Vacation",)
    assert chunks[-1].section == ("Expenses",)


def test_an_oversized_segment_leaves_no_stranded_tail() -> None:
    """A page a few tokens over the budget must not emit a tiny chunk of its own."""
    over_budget = " ".join(f"word{i}" for i in range(210))
    chunks = chunk_document(
        _doc(
            Segment(text=over_budget, page=1),
            Segment(text="A short paragraph that follows on the next page.", page=2),
        ),
        chunk_tokens=200,
        overlap_tokens=0,
    )
    assert all(c.tokens > 40 for c in chunks), [c.tokens for c in chunks]


def test_pages_are_reported_as_the_range_actually_covered() -> None:
    chunks = chunk_document(
        _doc(
            Segment(text="First page text.", page=3),
            Segment(text="Second page text.", page=4),
        ),
        chunk_tokens=400,
    )
    assert (chunks[0].page, chunks[0].page_end) == (3, 4)


def test_a_single_page_chunk_reports_no_range() -> None:
    (chunk,) = chunk_document(_doc(Segment(text="Only page.", page=7)), chunk_tokens=400)
    assert (chunk.page, chunk.page_end) == (7, None)


def test_breadcrumb_is_counted_against_the_budget() -> None:
    long_title = "a-very-long-document-name-that-eats-into-the-token-budget.md"
    document = _doc(
        Segment(text=" ".join(f"word{i}" for i in range(200)), section=("Vacation policy",))
    )
    chunks = chunk_document(document, title=long_title, chunk_tokens=64)
    assert all(c.tokens <= 64 for c in chunks), [c.tokens for c in chunks]


def test_no_title_and_no_section_means_no_breadcrumb() -> None:
    (chunk,) = chunk_document(_doc(Segment(text="Just a sentence about nothing.")))
    assert chunk.text == "Just a sentence about nothing."
    assert chunk.section == ()


# --- strategies -------------------------------------------------------


def test_per_unit_keeps_every_segment_separate() -> None:
    chunks = chunk_document(_doc(*SHORT), strategy=ChunkStrategy.PER_UNIT, chunk_tokens=400)
    assert len(chunks) == 3
    assert [c.section for c in chunks] == [s.section for s in SHORT]


def test_fixed_ignores_structure_and_claims_nothing() -> None:
    segments = [Segment(text=f"Sentence {i}." * 20, section=("S",), page=i) for i in range(5)]
    chunks = chunk_document(_doc(*segments), strategy=ChunkStrategy.FIXED, chunk_tokens=100)
    assert len(chunks) > 1
    assert all(c.section == () and c.page is None for c in chunks)
