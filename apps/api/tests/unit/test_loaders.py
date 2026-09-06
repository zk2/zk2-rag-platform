"""Document loaders: dispatch by extension and text normalisation."""

from __future__ import annotations

import json

import pytest

from zk2.sources.loaders import (
    SUPPORTED_EXTENSIONS,
    UnsupportedFormatError,
    detect_extension,
    load_bytes,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.PDF", "pdf"),
        ("notes.md", "md"),
        ("archive.tar.gz", "gz"),
        ("noextension", ""),
    ],
)
def test_detect_extension(filename: str, expected: str) -> None:
    assert detect_extension(filename) == expected


def test_txt_roundtrip() -> None:
    assert "hello" in load_bytes(b"hello world", "a.txt").text


def test_json_is_pretty_printed_and_keeps_unicode() -> None:
    payload = json.dumps({"key": "значение"}).encode()
    out = load_bytes(payload, "a.json")
    assert "значение" in out.text


def test_json_object_becomes_one_segment_per_key() -> None:
    payload = json.dumps({"alpha": 1, "beta": 2}).encode()
    doc = load_bytes(payload, "a.json")
    assert [s.section for s in doc.segments] == [("alpha",), ("beta",)]


def test_broken_json_falls_back_to_raw_text() -> None:
    assert "{not json" in load_bytes(b"{not json", "a.json").text


def test_html_strips_scripts_and_styles() -> None:
    html = b"<html><head><style>body{color:red}</style></head><body><p>Visible</p><script>alert(1)</script></body></html>"
    out = load_bytes(html, "page.html").text
    assert "Visible" in out
    assert "alert(1)" not in out
    assert "color:red" not in out


def test_html_sections_follow_headings() -> None:
    html = (
        b"<html><body><main>"
        b"<h1>Guide</h1><p>Intro line</p>"
        b"<h2>Setup</h2><p>Install it</p>"
        b"<h2>Usage</h2><p>Run it</p>"
        b"</main></body></html>"
    )
    doc = load_bytes(html, "page.html")
    assert [s.section for s in doc.segments] == [
        ("Guide",),
        ("Guide", "Setup"),
        ("Guide", "Usage"),
    ]


def test_html_main_wins_over_surrounding_chrome() -> None:
    html = (
        b"<html><body><div class='toolbar'>Sign in</div>"
        b"<main><p>Real content</p></main></body></html>"
    )
    doc = load_bytes(html, "page.html")
    assert "Real content" in doc.text
    assert "Sign in" not in doc.text


def test_html_table_keeps_its_rows() -> None:
    html = (
        b"<html><body><table>"
        b"<tr><th>Tool</th><th>Kind</th></tr>"
        b"<tr><td>Langfuse</td><td>Observability</td></tr>"
        b"</table></body></html>"
    )
    assert "Langfuse | Observability" in load_bytes(html, "page.html").text


def test_markdown_headings_become_sections() -> None:
    md = b"# Handbook\n\nWelcome.\n\n## Vacation\n\nTwenty days.\n"
    doc = load_bytes(md, "a.md")
    assert doc.title == "Handbook"
    assert [s.section for s in doc.segments] == [("Handbook",), ("Handbook", "Vacation")]


def test_numbered_list_items_are_not_headings() -> None:
    """The whole reason structure comes from the format and not from a regex."""
    md = b"## Pattern\n\n1. Router\nroutes the task\n2. Executor\nruns it\n"
    doc = load_bytes(md, "a.md")
    assert [s.section for s in doc.segments] == [("Pattern",)]


def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedFormatError):
        load_bytes(b"...", "malware.exe")


def test_supported_extensions_are_lowercase() -> None:
    assert all(ext == ext.lower() for ext in SUPPORTED_EXTENSIONS)
