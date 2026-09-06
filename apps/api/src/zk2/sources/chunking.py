"""Turning a parsed document into retrieval-sized chunks.

The loader hands over structure - pages, heading sections, sheets, keys - and
this module turns that into pieces of roughly one budget each.

The important word is *roughly one budget*. A segment boundary says "the
subject changes here", not "this is enough text to answer a question", so
neighbouring segments are **packed** together until the budget is reached, and
only what is still too big gets cut. Emitting one chunk per boundary instead
was the original mistake here: a corpus of exported notes came out at 63 chunks
averaging 119 tokens against a 400 budget, with a tail of 10-token fragments
holding a heading and one dangling line, because every item of every numbered
list opened a section of its own.

Metadata stays honest across a pack. `section` is the deepest heading path all
the packed segments share, and `page`/`page_end` the range they actually cover
- a chunk never claims a section or a page it only partly covers.

Token counts use cl100k_base. It is approximate for non-OpenAI models, which is
fine: chunk sizing needs to be consistent, not exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

import tiktoken

from zk2.sources.parsed import ParsedDocument, Segment
from zk2.sources.text_clean import fold_long_runs

DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 40
MIN_CHUNK_TOKENS = 100
MAX_CHUNK_TOKENS = 2000

#: Bumped whenever this pipeline produces different chunks for identical
#: settings - the chunker itself, or any loader feeding it. It is stamped on
#: every source at ingest, so a shipped change marks the corpus stale and
#: offers a reindex on its own. Without it a code change leaves every tenant
#: on a stale index with the reindex button greyed out, because the button
#: only ever compared *settings*.
#:
#:   1 - loaders emit structured segments, the chunker packs them up to the
#:       budget, headings come from the format instead of a regex over flat
#:       text, and chunks carry page numbers
CHUNKER_VERSION = 1

_BREADCRUMB_SEPARATOR = " > "
_SEPARATORS = ("\n\n", "\n", ". ", " ", "")
#: Fallback ratio, used only when a text is too short or too odd to measure.
CHARS_PER_TOKEN = 3.5


class ChunkStrategy(StrEnum):
    """How segment boundaries are treated.

    SEMANTIC  - pack neighbouring segments up to the budget, split anything
                still oversized at paragraph and sentence boundaries. The
                default, and the only one that actually aims for the budget.
    PER_UNIT  - one chunk per segment, never merged. Keeps a citation pinned
                to exactly one page or one heading, at the cost of chunks
                that are mostly far below the budget.
    FIXED     - ignore structure entirely and cut every N tokens. For uniform
                machine output where headings mean nothing.
    """

    SEMANTIC = "semantic"
    PER_UNIT = "per-unit"
    FIXED = "fixed"


@lru_cache(maxsize=1)
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Tokens in a text.

    Long single-character runs are folded first because the BPE is quadratic
    on them. Loaders already fold them out of the content, so this is the
    second line of defence for text that arrives from anywhere else. For such
    input the count is an underestimate, which is the right way to be wrong
    about junk.
    """
    return len(_enc().encode(fold_long_runs(text or "")))


def _tokenize(text: str) -> list[int]:
    return _enc().encode(text)


def _detokenize(tokens: list[int]) -> str:
    return _enc().decode(tokens)


def estimate_chars_per_token(text: str) -> float:
    """Chars per token measured on this text, for the splitter's first pass.

    A fixed ratio is wrong in both directions and the error compounds: English
    prose runs 4.8-5.7 chars per token, Cyrillic around 2.5. Too high a ratio
    overshoots into the token safety net and hard-slices mid-sentence, which
    means "split at meaning boundaries" silently does not happen at all.
    """
    sample = text[:4000]
    if not sample:
        return CHARS_PER_TOKEN
    tokens = count_tokens(sample)
    if tokens == 0:
        return CHARS_PER_TOKEN
    # A wild sample - one long word, a table of numbers - must not make the
    # budget absurd in either direction.
    return min(12.0, max(1.2, len(sample) / tokens))


@dataclass(slots=True)
class Chunk:
    ordinal: int
    text: str
    tokens: int
    section: tuple[str, ...] = field(default=())
    page: int | None = None
    page_end: int | None = None


# --- splitting one oversized piece ------------------------------------


def _split_keeping_separator(text: str, sep: str) -> list[str]:
    """Split on `sep`, keeping it attached to the piece it followed.

    Splitting it away and re-joining looks equivalent, but the last piece has
    nothing after it to re-join against, so a ". " separator silently eats the
    full stop - one character of the source gone at every chunk boundary.
    """
    parts = text.split(sep)
    out: list[str] = []
    for i, part in enumerate(parts):
        piece = part if i == len(parts) - 1 else part + sep
        if piece:
            out.append(piece)
    return out


def _slice_fixed(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _merge_with_overlap(splits: list[str], budget: int, overlap: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def push() -> None:
        merged = "".join(buf).strip()
        if merged:
            out.append(merged)

    for piece in splits:
        if buf and buf_len + len(piece) > budget:
            push()
            buf = _tail_overlap(buf, overlap)
            buf_len = sum(len(p) for p in buf)
        buf.append(piece)
        buf_len += len(piece)
    push()
    return out


def _tail_overlap(buf: list[str], overlap: int) -> list[str]:
    if overlap <= 0:
        return []
    out: list[str] = []
    total = 0
    for piece in reversed(buf):
        if total + len(piece) > overlap and out:
            break
        out.insert(0, piece)
        total += len(piece)
    return out


def _recurse(text: str, seps: tuple[str, ...], budget: int, overlap: int) -> list[str]:
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    chosen = ""
    chosen_index = len(seps) - 1
    for i, sep in enumerate(seps):
        if sep == "" or sep in text:
            chosen, chosen_index = sep, i
            break

    parts = _slice_fixed(text, budget) if chosen == "" else _split_keeping_separator(text, chosen)

    good: list[str] = []
    tail = seps[chosen_index + 1 :] or ("",)
    for part in parts:
        if not part:
            continue
        if len(part) <= budget:
            good.append(part)
        else:
            good.extend(_recurse(part, tail, budget, overlap))

    return _merge_with_overlap(good, budget, overlap)


def split_text(text: str, *, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    """Cut one piece of text at the best boundary that fits the budget.

    Paragraph first, then line, sentence, word, and only as a last resort the
    raw character. The recursion works in characters - cheap, no tokenizer call
    per candidate split - and every result is then validated against the real
    token budget, with anything still over it sliced at the token level.
    """
    if not text.strip():
        return []
    chars_per_token = estimate_chars_per_token(text)
    char_budget = max(1, int(chunk_tokens * chars_per_token))
    char_overlap = max(0, int(overlap_tokens * chars_per_token))

    out: list[str] = []
    for piece in _recurse(text, _SEPARATORS, char_budget, char_overlap):
        if count_tokens(piece) <= chunk_tokens:
            if piece:
                out.append(piece)
        else:
            out.extend(_token_slice(piece, chunk_tokens, overlap_tokens))
    return _rebalance_tail([p for p in out if p], chunk_tokens, overlap_tokens)


def _rebalance_tail(pieces: list[str], chunk_tokens: int, overlap_tokens: int) -> list[str]:
    """Spread the last two pieces evenly when the last one came out tiny.

    Filling greedily up to the budget leaves whatever is left as the final
    piece, and "whatever is left" is regularly twenty tokens. Two pieces of a
    hundred are worth more to retrieval than one of a hundred and eighty and
    one of twenty.
    """
    if len(pieces) < 2 or count_tokens(pieces[-1]) >= chunk_tokens // 4:
        return pieces
    combined = f"{pieces[-2]} {pieces[-1]}"
    return pieces[:-2] + _token_slice(combined, chunk_tokens, overlap_tokens)


def _token_slice(text: str, size: int, overlap: int) -> list[str]:
    """Cut at token positions, in pieces of even length.

    Slicing at a fixed stride and letting the last piece be whatever is left
    strands a remainder - 214 tokens against a budget of 200 gives a chunk of
    200 and a chunk of 14, and the second one is worth nothing to retrieval.
    Spreading the same text over the same number of pieces gives two of 107.
    """
    ids = _tokenize(text)
    stride = max(1, size - overlap)
    pieces = max(1, -(-len(ids) // stride))
    even_stride = max(1, -(-len(ids) // pieces))
    even_size = min(size, even_stride + overlap)

    out: list[str] = []
    for start in range(0, len(ids), even_stride):
        piece = _detokenize(ids[start : start + even_size]).strip()
        if piece:
            out.append(piece)
        if start + even_size >= len(ids):
            break
    return out


# --- packing segments into chunks -------------------------------------


def _breadcrumb(title: str | None, section: tuple[str, ...]) -> str:
    parts = [p for p in (title, *section) if p]
    return _BREADCRUMB_SEPARATOR.join(parts)


def _common_section(sections: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Deepest heading path every packed segment shares.

    Segments with no path at all collapse the result to nothing, which is
    correct: there is no heading the chunk can honestly claim.
    """
    if not sections:
        return ()
    common = sections[0]
    for section in sections[1:]:
        depth = 0
        while depth < len(common) and depth < len(section) and common[depth] == section[depth]:
            depth += 1
        common = common[:depth]
        if not common:
            return ()
    return common


class _Emitter:
    """Collects chunks, numbering them and prefixing the breadcrumb."""

    def __init__(self, title: str | None, chunk_tokens: int, overlap_tokens: int) -> None:
        self.title = title
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens
        self.chunks: list[Chunk] = []

    def emit(
        self,
        body: str,
        *,
        section: tuple[str, ...],
        page: int | None = None,
        page_end: int | None = None,
    ) -> None:
        body = body.strip()
        if not body:
            return
        breadcrumb = _breadcrumb(self.title, section)
        text = f"{breadcrumb}\n{body}" if breadcrumb else body
        tokens = count_tokens(text)
        if tokens > self.chunk_tokens:
            # The packer works from running counts, and a running count is a
            # little short of what the tokenizer makes of the joined text. This
            # is where the budget is actually enforced, so nothing downstream
            # has to trust the estimate.
            budget = _budget_for(self.chunk_tokens, breadcrumb)
            for piece in split_text(body, chunk_tokens=budget, overlap_tokens=self.overlap_tokens):
                self._append(piece, breadcrumb, section, page, page_end)
            return
        self._append(body, breadcrumb, section, page, page_end)

    def _append(
        self,
        body: str,
        breadcrumb: str,
        section: tuple[str, ...],
        page: int | None,
        page_end: int | None,
    ) -> None:
        text = f"{breadcrumb}\n{body}" if breadcrumb else body
        self.chunks.append(
            Chunk(
                ordinal=len(self.chunks),
                text=text,
                tokens=count_tokens(text),
                section=section,
                page=page,
                page_end=page_end if page_end != page else None,
            )
        )


def _budget_for(chunk_tokens: int, breadcrumb: str) -> int:
    """Room left for the body once the breadcrumb has taken its share.

    The breadcrumb rides along in every chunk, so it comes out of the budget.
    A very long one is capped rather than allowed to starve the body.
    """
    prefix_tokens = count_tokens(breadcrumb) + 1 if breadcrumb else 0
    return max(chunk_tokens - prefix_tokens, chunk_tokens // 2)


def _sized_segments(segments: list[Segment], emitter: _Emitter) -> list[Segment]:
    """Every segment cut down to something that can fit one chunk.

    Splitting up front rather than mid-pack matters: a page that runs a few
    tokens over the budget used to close the pack, emit its bulk, and leave its
    short tail stranded as a chunk of its own. As ordinary segments the pieces
    pack with their neighbours like anything else.
    """
    out: list[Segment] = []
    for segment in segments:
        budget = _budget_for(emitter.chunk_tokens, _breadcrumb(emitter.title, segment.section))
        if count_tokens(segment.text) <= budget:
            out.append(segment)
            continue
        pieces = split_text(
            segment.text, chunk_tokens=budget, overlap_tokens=emitter.overlap_tokens
        )
        out.extend(
            Segment(text=piece, page=segment.page, section=segment.section) for piece in pieces
        )
    return out


def _section_floor(chunk_tokens: int) -> int:
    """Below this, a chunk is too small to be worth its own heading path."""
    return chunk_tokens // 2


def _costs_the_section(buffer: list[Segment], segment: Segment) -> bool:
    """Would adding `segment` shorten the heading path the pack can claim?"""
    current = _common_section([s.section for s in buffer])
    if not current:
        return False
    return _common_section([*(s.section for s in buffer), segment.section]) != current


def _chunk_semantic(segments: list[Segment], emitter: _Emitter) -> None:
    buffer: list[Segment] = []
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        section = _common_section([s.section for s in buffer])
        pages = [s.page for s in buffer if s.page is not None]
        emitter.emit(
            "\n\n".join(s.text for s in buffer),
            section=section,
            page=pages[0] if pages else None,
            page_end=pages[-1] if pages else None,
        )
        buffer, buffer_tokens = [], 0

    for segment in _sized_segments(segments, emitter):
        # The budget shrinks by the breadcrumb, and packing more segments can
        # only shorten a shared path - so the first segment's own breadcrumb is
        # a safe upper bound for what the pack will end up carrying.
        head = buffer[0] if buffer else segment
        budget = _budget_for(emitter.chunk_tokens, _breadcrumb(emitter.title, head.section))
        segment_tokens = count_tokens(segment.text)

        if buffer and buffer_tokens + segment_tokens + 2 > budget:
            flush()
        elif (
            buffer
            and _costs_the_section(buffer, segment)
            and buffer_tokens >= _section_floor(emitter.chunk_tokens)
        ):
            # Packing on would shorten the path this chunk can honestly claim.
            # Worth it while the chunk is still small - a 30-token fragment
            # with a perfect breadcrumb is no use to anyone - but once there is
            # a chunk's worth of text, the heading is worth more than the room.
            flush()
        buffer.append(segment)
        buffer_tokens += segment_tokens + (2 if len(buffer) > 1 else 0)

    flush()


def _chunk_per_unit(segments: list[Segment], emitter: _Emitter) -> None:
    for segment in segments:
        emitter.emit(segment.text, section=segment.section, page=segment.page)


def _chunk_fixed(segments: list[Segment], emitter: _Emitter) -> None:
    # A fixed cut lands wherever it lands, so it claims no section and no page:
    # naming the heading it started under would be a lie half the time.
    budget = _budget_for(emitter.chunk_tokens, _breadcrumb(emitter.title, ()))
    joined = "\n\n".join(s.text for s in segments)
    for piece in _token_slice(joined, budget, emitter.overlap_tokens):
        emitter.emit(piece, section=())


def chunk_document(
    document: ParsedDocument,
    *,
    title: str | None = None,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    strategy: ChunkStrategy = ChunkStrategy.SEMANTIC,
) -> list[Chunk]:
    """Split a parsed document into chunks that carry their own context."""
    if chunk_tokens <= 0:
        msg = "chunk_tokens must be positive"
        raise ValueError(msg)
    if overlap_tokens >= chunk_tokens:
        msg = "overlap_tokens must be smaller than chunk_tokens"
        raise ValueError(msg)

    segments = [s for s in document.segments if s.text.strip()]
    if not segments:
        return []

    emitter = _Emitter(title, chunk_tokens, overlap_tokens)
    if strategy is ChunkStrategy.PER_UNIT:
        _chunk_per_unit(segments, emitter)
    elif strategy is ChunkStrategy.FIXED:
        _chunk_fixed(segments, emitter)
    else:
        _chunk_semantic(segments, emitter)
    return emitter.chunks


def chunk_text(
    text: str,
    *,
    title: str | None = None,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Chunk plain text that never went through a loader."""
    return chunk_document(
        ParsedDocument(segments=[Segment(text=text)] if text.strip() else []),
        title=title,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
    )


_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(?P<title>\S.*?)\s*#*$")


def markdown_heading(line: str) -> tuple[int, str] | None:
    """(level, title) when a line is an ATX markdown heading.

    Lives here because both the markdown loader and the HTML loader describe
    headings the same way, and nothing else is allowed to guess at one.
    """
    match = _MARKDOWN_HEADING.match(line.strip())
    return (len(match.group(1)), match.group("title")) if match else None
