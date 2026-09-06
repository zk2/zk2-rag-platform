"""Structure read out of a PDF, which declares none of it.

A PDF has no headings, only type. These tests build one the way an office
suite does - a large bold line for a section, ordinary type for the body,
including a numbered list - and pin what the loader must make of it.
"""

from __future__ import annotations

import pytest

from zk2.sources.chunking import chunk_document
from zk2.sources.loaders import load_bytes

pytestmark = pytest.mark.unit

BODY = 12
HEADING = 24
SUBHEADING = 18


def _pdf(pages: list[list[tuple[str, int]]]) -> bytes:
    """Build a PDF from (line, type size) pairs, one list per page."""
    import pymupdf

    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 72.0
        for text, size in lines:
            page.insert_text((72, y), text, fontsize=size)
            y += size * 1.6
    data: bytes = doc.tobytes()
    doc.close()
    return data


PATTERN_PAGE = [
    ("18. A good production pattern", HEADING),
    ("I would design it like this:", BODY),
    ("1. Router", BODY),
    ("decides the type of the task", BODY),
    ("2. Deterministic workflow if possible", BODY),
    ("when the path is known we do not start an agent", BODY),
    ("3. Agent only for dynamic parts", BODY),
    ("the agent picks tools only where that is needed", BODY),
]


def test_numbered_list_items_do_not_become_sections() -> None:
    """The defect this rewrite exists for: nine list items, nine useless chunks."""
    document = load_bytes(_pdf([PATTERN_PAGE]), "agents.pdf")
    sections = {segment.section for segment in document.segments}
    assert sections == {("18. A good production pattern",)}


def test_the_list_stays_in_one_piece() -> None:
    chunks = chunk_document(load_bytes(_pdf([PATTERN_PAGE]), "agents.pdf"), title="agents.pdf")
    assert len(chunks) == 1
    assert "1. Router" in chunks[0].text
    assert "3. Agent only for dynamic parts" in chunks[0].text
    assert chunks[0].text.startswith("agents.pdf > 18. A good production pattern\n")


def test_type_size_gives_the_heading_level() -> None:
    document = load_bytes(
        _pdf(
            [
                [
                    ("2. Full pipeline", HEADING),
                    ("Overview of the whole thing.", BODY),
                    ("2.1. Ingest", SUBHEADING),
                    ("Read the bytes and get text out of them.", BODY),
                ]
            ]
        ),
        "rag.pdf",
    )
    assert [segment.section for segment in document.segments] == [
        ("2. Full pipeline",),
        ("2. Full pipeline", "2.1. Ingest"),
    ]


def test_segments_carry_the_page_they_came_from() -> None:
    document = load_bytes(
        _pdf([[("First page body text.", BODY)], [("Second page body text.", BODY)]]),
        "two.pdf",
    )
    assert [segment.page for segment in document.segments] == [1, 2]
    assert document.page_count == 2


def test_a_table_of_contents_is_not_indexed() -> None:
    """Dot leaders match every query that quotes a section title and answer none."""
    document = load_bytes(
        _pdf(
            [
                [
                    ("Table of Contents", SUBHEADING),
                    ("1. What is RAG" + "." * 90 + "2", BODY),
                    ("2. Chunking strategies" + "." * 80 + "7", BODY),
                ],
                [
                    ("1. What is RAG", HEADING),
                    ("Retrieval augmented generation, in one line.", BODY),
                ],
            ]
        ),
        "rag.pdf",
    )
    assert all("Table of Contents" not in segment.section for segment in document.segments)
    assert "Retrieval augmented generation" in document.text


def test_a_document_with_one_type_size_has_no_sections() -> None:
    document = load_bytes(_pdf([[("Plain prose, all one size.", BODY)]]), "flat.pdf")
    assert [segment.section for segment in document.segments] == [()]
