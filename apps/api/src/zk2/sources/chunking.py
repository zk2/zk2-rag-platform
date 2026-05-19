"""Text chunking with tiktoken-accurate token counts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 40


@lru_cache(maxsize=1)
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc().encode(text or ""))


@dataclass(slots=True)
class Chunk:
    ordinal: int
    text: str
    tokens: int


def chunk_text(
    text: str,
    *,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Token-based chunking.

    Uses cl100k_base BPE for splitting; this is approximate for non-OpenAI models
    but good enough for retrieval-quality chunking. Splits along token boundaries
    with `overlap_tokens` of overlap between consecutive chunks.
    """
    if not text or not text.strip():
        return []
    enc = _enc()
    tokens = enc.encode(text)
    if not tokens:
        return []

    if chunk_tokens <= 0:
        msg = "chunk_tokens must be positive"
        raise ValueError(msg)
    if overlap_tokens >= chunk_tokens:
        msg = "overlap_tokens must be smaller than chunk_tokens"
        raise ValueError(msg)

    stride = chunk_tokens - overlap_tokens
    chunks: list[Chunk] = []
    ordinal = 0
    start = 0
    while start < len(tokens):
        end = min(start + chunk_tokens, len(tokens))
        piece = tokens[start:end]
        chunk_str = enc.decode(piece).strip()
        if chunk_str:
            chunks.append(Chunk(ordinal=ordinal, text=chunk_str, tokens=len(piece)))
            ordinal += 1
        if end == len(tokens):
            break
        start += stride
    return chunks
