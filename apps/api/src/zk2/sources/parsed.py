"""What a loader hands to the chunker.

A loader's job is structure, not size. It splits a document along the
boundaries the format actually provides - PDF pages and heading levels, HTML
sections, markdown headings, spreadsheet sheets, JSON keys - and says where
each piece came from. Deciding how much text belongs in one chunk is the
chunker's job, and it needs those boundaries to decide well.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Segment:
    """One structural unit of a document."""

    text: str
    #: 1-based page, when the format has pages. Citations quote it.
    page: int | None = None
    #: Heading path this text sits under, outermost first.
    section: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    segments: list[Segment] = field(default_factory=list)
    #: Title the format declares (PDF metadata, first markdown H1), if any.
    title: str | None = None
    page_count: int | None = None

    @property
    def text(self) -> str:
        """The whole document as plain text. For callers that want no structure."""
        return "\n\n".join(s.text for s in self.segments)
