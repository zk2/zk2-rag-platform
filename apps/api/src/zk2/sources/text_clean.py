"""Text-cleanup primitives shared by every loader.

Kept tiny and pure so they are trivially correct, and applied before any
storage write: what reaches the chunker has already been through here.
"""

from __future__ import annotations

import re

_NUL_RE = re.compile("\x00")
_CRLF_RE = re.compile(r"\r\n?")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_INLINE_WS_RE = re.compile(r"[ \t]{2,}")

#: Longest run of one repeated character that survives cleanup.
MAX_RUN = 32
_LONG_RUN_RE = re.compile(rf"(.)\1{{{MAX_RUN},}}")


def strip_null_bytes(text: str) -> str:
    """Drop NUL bytes.

    PDFs and badly encoded files carry them, and Postgres refuses them inside
    a `text` column ("invalid byte sequence for encoding UTF8: 0x00"), so an
    otherwise fine document would fail at the very last step of ingest.
    """
    return _NUL_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    """One line ending, no trailing spaces, at most one blank line.

    Blank lines are kept: they are the paragraph boundary the splitter cuts on.
    """
    text = _CRLF_RE.sub("\n", text)
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return _INLINE_WS_RE.sub(" ", text)


def fold_long_runs(text: str) -> str:
    """Fold a run of one repeated character down to MAX_RUN copies.

    For retrieval these runs carry nothing - a rule of 200 dashes means what a
    rule of 32 means, and the dot leaders in a table of contents mean nothing
    at all. Operationally they are worse than nothing: BPE tokenization is
    quadratic on them, and the worker runs every queue in one process, so one
    upload of a file that decodes to a solid run of U+FFFD stalls indexing for
    every tenant while it counts tokens.
    """
    return _LONG_RUN_RE.sub(lambda m: m.group(1) * MAX_RUN, text)


def clean_text(text: str) -> str:
    return fold_long_runs(normalize_whitespace(strip_null_bytes(text))).strip()
