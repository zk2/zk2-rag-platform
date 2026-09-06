"""Reading a chunk's position out of the metadata the ingest wrote."""

from __future__ import annotations

from typing import Any


def chunk_location(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Page range and heading path a chunk covers, as retriever kwargs.

    Chunks written before pages existed carry none of these keys, so every
    lookup defaults rather than raising: an old index keeps working, it just
    cites a little less precisely.
    """
    meta = meta or {}
    page = meta.get("page")
    page_end = meta.get("page_end")
    section = meta.get("section") or ()
    return {
        "page": int(page) if isinstance(page, int) else None,
        "page_end": int(page_end) if isinstance(page_end, int) else None,
        "section": tuple(str(part) for part in section),
    }
