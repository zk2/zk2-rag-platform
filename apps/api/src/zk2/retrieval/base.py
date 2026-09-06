"""Shared retrieval types.

Every retriever returns the same shape so fusion can mix them: a chunk plus a
score where higher is better. The scales differ (cosine similarity, ts_rank_cd,
RRF weight), which is exactly why fusion works on ranks rather than values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    source_id: int
    source_name: str
    ordinal: int
    text: str
    score: float
    retriever: str
    # Which retrievers surfaced this chunk; filled in by fusion
    matched_by: tuple[str, ...] = field(default=())
    # Where in the document it sits, for a citation that can be looked up
    page: int | None = None
    page_end: int | None = None
    section: tuple[str, ...] = field(default=())

    @property
    def location(self) -> str:
        """Human-readable position: the page, or the heading path, or neither."""
        if self.page is not None:
            return f"pages {self.page}-{self.page_end}" if self.page_end else f"page {self.page}"
        return " > ".join(self.section)
