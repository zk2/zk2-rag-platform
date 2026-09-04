"""Structure-aware chunking.

Cutting a document every 400 tokens splits sentences mid-clause and drops the
heading a passage lived under, which is exactly the context a retrieved chunk
needs to stand on its own. Instead:

* headings are tracked, so every chunk knows the section it came from
* paragraphs are kept whole where they fit, sentences where they do not, and
  only an oversized sentence falls back to a raw token window
* each chunk is prefixed with a breadcrumb (document title and heading path)
  before it is embedded and indexed - cheap, and it helps both dense and
  lexical retrieval tell similar passages apart

Token counts use cl100k_base. It is approximate for non-OpenAI models, which is
fine: chunk sizing needs to be consistent, not exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken

DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 40

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(?P<title>\S.*?)\s*#*$")
# "1.", "1.2", "IV." followed by a short title-like line - common in exports
_NUMBERED_HEADING = re.compile(r"^(?P<num>\d+(\.\d+)*\.?|[IVXLC]+\.)\s+(?P<title>\S.{0,80})$")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n")
_BREADCRUMB_SEPARATOR = " > "
_MAX_HEADING_CHARS = 90


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
    heading_path: tuple[str, ...] = field(default=())


@dataclass(slots=True)
class _Block:
    text: str
    heading_path: tuple[str, ...]


def _detect_heading(line: str) -> tuple[int, str] | None:
    """Return (level, title) when the line reads as a heading."""
    stripped = line.strip()
    if not stripped:
        return None
    markdown = _MARKDOWN_HEADING.match(stripped)
    if markdown:
        return len(markdown.group(1)), markdown.group("title")
    numbered = _NUMBERED_HEADING.match(stripped)
    if numbered and len(stripped) <= _MAX_HEADING_CHARS and not stripped.endswith((",", ";", ":")):
        depth = numbered.group("num").rstrip(".").count(".") + 1
        return depth, numbered.group("title")
    return None


def _split_blocks(text: str) -> list[_Block]:
    """Paragraphs, each tagged with the heading path it sits under."""
    blocks: list[_Block] = []
    heading_stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        paragraph = "\n".join(buffer).strip()
        buffer.clear()
        if paragraph:
            blocks.append(_Block(paragraph, tuple(title for _, title in heading_stack)))

    for raw_line in text.splitlines():
        heading = _detect_heading(raw_line)
        if heading is not None:
            flush()
            level, title = heading
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            continue
        if not raw_line.strip():
            flush()
            continue
        buffer.append(raw_line)
    flush()
    return blocks


def _split_sentences(paragraph: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(paragraph) if p and p.strip()]
    return parts or [paragraph]


def _token_windows(text: str, *, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    """Last resort for a single unit that exceeds the budget on its own."""
    enc = _enc()
    tokens = enc.encode(text)
    stride = max(chunk_tokens - overlap_tokens, 1)
    windows: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_tokens, len(tokens))
        piece = enc.decode(tokens[start:end]).strip()
        if piece:
            windows.append(piece)
        if end == len(tokens):
            break
        start += stride
    return windows


def _breadcrumb(title: str | None, heading_path: tuple[str, ...]) -> str:
    return _BREADCRUMB_SEPARATOR.join(p for p in (title, *heading_path) if p)


def _sized_units(paragraph: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    if count_tokens(paragraph) <= chunk_tokens:
        return [paragraph]
    units: list[str] = []
    for sentence in _split_sentences(paragraph):
        if count_tokens(sentence) <= chunk_tokens:
            units.append(sentence)
        else:
            units.extend(
                _token_windows(sentence, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)
            )
    return units


def _grouped_units(
    text: str, chunk_tokens: int, overlap_tokens: int
) -> list[tuple[tuple[str, ...], list[tuple[str, int]]]]:
    """Text as runs of (heading path, [(unit, tokens)]) within one section."""
    grouped: list[tuple[tuple[str, ...], list[tuple[str, int]]]] = []
    for block in _split_blocks(text):
        units = [
            (unit, count_tokens(unit))
            for unit in _sized_units(block.text, chunk_tokens, overlap_tokens)
        ]
        if not units:
            continue
        if grouped and grouped[-1][0] == block.heading_path:
            grouped[-1][1].extend(units)
        else:
            grouped.append((block.heading_path, units))
    return grouped


def _overlap_tail(units: list[str], overlap_tokens: int) -> list[str]:
    """Trailing units of the previous chunk, carried over for continuity."""
    if overlap_tokens <= 0:
        return []
    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        unit_tokens = count_tokens(unit)
        if total + unit_tokens > overlap_tokens:
            break
        tail.insert(0, unit)
        total += unit_tokens
    return tail


def _make_chunk(
    ordinal: int, units: list[str], breadcrumb: str, heading_path: tuple[str, ...]
) -> Chunk:
    body = "\n".join(units).strip()
    text = f"{breadcrumb}\n{body}" if breadcrumb else body
    return Chunk(ordinal=ordinal, text=text, tokens=count_tokens(text), heading_path=heading_path)


def chunk_document(
    text: str,
    *,
    title: str | None = None,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split a document into retrieval-sized chunks that carry their context."""
    if chunk_tokens <= 0:
        msg = "chunk_tokens must be positive"
        raise ValueError(msg)
    if overlap_tokens >= chunk_tokens:
        msg = "overlap_tokens must be smaller than chunk_tokens"
        raise ValueError(msg)
    if not text or not text.strip():
        return []

    chunks: list[Chunk] = []
    ordinal = 0
    for heading_path, units in _grouped_units(text, chunk_tokens, overlap_tokens):
        breadcrumb = _breadcrumb(title, heading_path)
        # The breadcrumb rides along in every chunk, so it comes out of the budget
        prefix_tokens = count_tokens(breadcrumb) + 1 if breadcrumb else 0
        budget = max(chunk_tokens - prefix_tokens, chunk_tokens // 2)

        current: list[str] = []
        current_tokens = 0
        for unit, unit_tokens in units:
            if current and current_tokens + unit_tokens > budget:
                chunks.append(_make_chunk(ordinal, current, breadcrumb, heading_path))
                ordinal += 1
                current = _overlap_tail(current, overlap_tokens)
                current_tokens = sum(count_tokens(part) for part in current)
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            chunks.append(_make_chunk(ordinal, current, breadcrumb, heading_path))
            ordinal += 1
    return chunks


def chunk_text(
    text: str,
    *,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Chunk without a document title. Kept for callers that have none."""
    return chunk_document(
        text, title=None, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens
    )
