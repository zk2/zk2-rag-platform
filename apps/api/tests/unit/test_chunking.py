"""Token-based chunking."""

from __future__ import annotations

import pytest

from zk2.sources.chunking import (
    DEFAULT_CHUNK_TOKENS,
    chunk_document,
    chunk_text,
    count_tokens,
)

pytestmark = pytest.mark.unit


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
    # The tail of one chunk must reappear at the head of the next
    first_tail = chunks[0].text.split()[-5:]
    assert any(token in chunks[1].text for token in first_tail)


def test_no_overlap_when_disabled() -> None:
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, chunk_tokens=50, overlap_tokens=0)
    joined = " ".join(c.text for c in chunks).split()
    assert len(joined) == len(set(joined)), "words repeat despite zero overlap"


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError, match="chunk_tokens must be positive"):
        chunk_text("text", chunk_tokens=0)
    with pytest.raises(ValueError, match="overlap_tokens must be smaller"):
        chunk_text("text", chunk_tokens=10, overlap_tokens=10)


def test_default_chunk_size_is_respected() -> None:
    text = " ".join(f"word{i}" for i in range(5000))
    chunks = chunk_text(text)
    assert all(c.tokens <= DEFAULT_CHUNK_TOKENS for c in chunks)


# ─── Structure awareness ──────────────────────────────────────────────

DOCUMENT = """# Employee Handbook

Welcome to the company.

## Vacation policy

Employees accrue 20 days per year.
Unused days roll over once.

## Expenses

Receipts must be submitted within 30 days.
"""


def test_headings_become_breadcrumbs() -> None:
    chunks = chunk_document(DOCUMENT, title="handbook.md")
    vacation = next(c for c in chunks if "accrue 20 days" in c.text)
    assert vacation.text.startswith("handbook.md > Employee Handbook > Vacation policy\n")
    assert vacation.heading_path == ("Employee Handbook", "Vacation policy")


def test_sibling_sections_do_not_inherit_each_other() -> None:
    chunks = chunk_document(DOCUMENT, title="handbook.md")
    expenses = next(c for c in chunks if "Receipts" in c.text)
    assert expenses.heading_path == ("Employee Handbook", "Expenses")


def test_sections_are_not_merged_into_one_chunk() -> None:
    """Two topics under different headings must not share a chunk."""
    chunks = chunk_document(DOCUMENT, title="handbook.md")
    assert not any("accrue 20 days" in c.text and "Receipts" in c.text for c in chunks)


def test_no_title_and_no_headings_means_no_breadcrumb() -> None:
    (chunk,) = chunk_document("Just a sentence about nothing in particular.")
    assert chunk.text == "Just a sentence about nothing in particular."
    assert chunk.heading_path == ()


def test_numbered_headings_are_recognised() -> None:
    text = "1. Scope\n\nThis agreement covers all services.\n\n2. Payment\n\nNet 30 days.\n"
    chunks = chunk_document(text, title="contract.txt")
    assert [c.heading_path for c in chunks] == [("Scope",), ("Payment",)]


def test_paragraph_is_kept_whole_when_it_fits() -> None:
    paragraph = "Sentence one. Sentence two. Sentence three."
    (chunk,) = chunk_document(paragraph, chunk_tokens=100)
    assert chunk.text == paragraph


def test_long_paragraph_is_split_on_sentence_boundaries() -> None:
    sentences = [f"This is sentence number {i} in a long paragraph." for i in range(60)]
    chunks = chunk_document(" ".join(sentences), chunk_tokens=60, overlap_tokens=0)
    assert len(chunks) > 1
    # No chunk starts or ends mid-sentence
    for chunk in chunks:
        assert chunk.text.strip().endswith(".")


def test_overlap_repeats_a_whole_sentence() -> None:
    sentences = [f"Sentence {i} carries its own meaning." for i in range(40)]
    chunks = chunk_document(" ".join(sentences), chunk_tokens=60, overlap_tokens=20)
    last_line_of_first = chunks[0].text.strip().splitlines()[-1]
    assert last_line_of_first in chunks[1].text
    assert chunks[1].text.splitlines()[0] != chunks[0].text.splitlines()[0]


def test_breadcrumb_is_counted_against_the_budget() -> None:
    long_title = "a-very-long-document-name-that-eats-into-the-token-budget.md"
    chunks = chunk_document(DOCUMENT, title=long_title, chunk_tokens=64)
    assert all(c.tokens <= 64 for c in chunks), [c.tokens for c in chunks]


def test_ordinals_are_sequential_across_sections() -> None:
    chunks = chunk_document(DOCUMENT, title="handbook.md", chunk_tokens=60, overlap_tokens=10)
    assert len(chunks) >= 3
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
