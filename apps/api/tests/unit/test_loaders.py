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
    assert "hello" in load_bytes(b"hello world", "a.txt")


def test_json_is_pretty_printed_and_keeps_unicode() -> None:
    payload = json.dumps({"key": "значение"}).encode()
    out = load_bytes(payload, "a.json")
    assert "значение" in out


def test_broken_json_falls_back_to_raw_text() -> None:
    assert "{not json" in load_bytes(b"{not json", "a.json")


def test_html_strips_scripts_and_styles() -> None:
    html = b"<html><head><style>body{color:red}</style></head><body><p>Visible</p><script>alert(1)</script></body></html>"
    out = load_bytes(html, "page.html")
    assert "Visible" in out
    assert "alert(1)" not in out
    assert "color:red" not in out


def test_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedFormatError):
        load_bytes(b"...", "malware.exe")


def test_supported_extensions_are_lowercase() -> None:
    assert all(ext == ext.lower() for ext in SUPPORTED_EXTENSIONS)
