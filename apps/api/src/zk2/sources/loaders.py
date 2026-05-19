"""Document loaders. Each loader takes bytes + filename and returns plain text."""

from __future__ import annotations

import io
import json
import re
from typing import Final

import structlog

logger = structlog.get_logger()

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {"pdf", "txt", "md", "json", "docx", "xlsx", "html", "htm"}
)


class UnsupportedFormatError(ValueError):
    pass


def detect_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def load_bytes(data: bytes, filename: str) -> str:
    """Return plain text extracted from `data` based on filename extension."""
    ext = detect_extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(f"Unsupported extension: {ext!r}")

    loader = _LOADERS[ext]
    text = loader(data)
    return _normalize(text)


def _load_txt(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _load_md(data: bytes) -> str:
    # Plain markdown — strip nothing, embedding model handles markup fine.
    return data.decode("utf-8", errors="replace")


def _load_json(data: bytes) -> str:
    try:
        parsed = json.loads(data)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return data.decode("utf-8", errors="replace")


def _load_pdf(data: bytes) -> str:
    import pymupdf  # type: ignore[import-not-found]

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _load_docx(data: bytes) -> str:
    from docx import Document  # type: ignore[import-not-found]

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)


def _load_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook  # type: ignore[import-not-found]

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                parts.append("\t".join(cells))
    return "\n".join(parts)


def _load_html(data: bytes) -> str:
    from selectolax.parser import HTMLParser  # type: ignore[import-not-found]

    tree = HTMLParser(data.decode("utf-8", errors="replace"))
    for selector in ("script", "style", "noscript", "iframe"):
        for node in tree.css(selector):
            node.decompose()
    body = tree.body
    text = body.text(separator="\n", strip=True) if body else tree.text(strip=True)
    return text


_LOADERS: Final[dict[str, callable]] = {  # type: ignore[type-arg]
    "txt": _load_txt,
    "md": _load_md,
    "json": _load_json,
    "pdf": _load_pdf,
    "docx": _load_docx,
    "xlsx": _load_xlsx,
    "html": _load_html,
    "htm": _load_html,
}


_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
