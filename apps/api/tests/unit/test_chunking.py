"""Token-based chunking."""

from __future__ import annotations

import pytest

from zk2.sources.chunking import DEFAULT_CHUNK_TOKENS, chunk_text, count_tokens

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
