"""Language detection for indexed text.

Only enough to pick a Postgres text search configuration. A full detector
(lingua, fastText) would be a heavy dependency for a decision with three
practical outcomes here, and a wrong guess costs stemming quality, not
correctness - retrieval still works through the `simple` configuration.

Postgres ships configurations for a fixed set of languages; Ukrainian is not
among them, so it falls back to `simple` (no stemming, no stop words) rather
than being stemmed by the Russian rules, which would be worse than nothing.
"""

from __future__ import annotations

from typing import Final

DEFAULT_LANG: Final = "en"
DEFAULT_CONFIG: Final = "simple"

# Postgres text search configurations that ship with a stock server.
_CONFIG_BY_LANG: Final[dict[str, str]] = {
    "en": "english",
    "ru": "russian",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    # No stock configuration - `simple` beats stemming with the wrong rules
    "uk": "simple",
}

# Letters that appear in Ukrainian but not in Russian, and vice versa.
_UKRAINIAN_ONLY: Final[frozenset[str]] = frozenset("їієґ")
_RUSSIAN_ONLY: Final[frozenset[str]] = frozenset("ыэъё")

_SAMPLE_CHARS: Final = 4000


def detect_language(text: str) -> str:
    """Best-effort two-letter code for a document."""
    sample = text[:_SAMPLE_CHARS].lower()
    cyrillic = sum(1 for ch in sample if "Ѐ" <= ch <= "ӿ")
    latin = sum(1 for ch in sample if "a" <= ch <= "z")
    if cyrillic == 0 and latin == 0:
        return DEFAULT_LANG
    if cyrillic <= latin:
        return DEFAULT_LANG

    letters = set(sample)
    if letters & _UKRAINIAN_ONLY:
        return "uk"
    if letters & _RUSSIAN_ONLY:
        return "ru"
    # Cyrillic without a distinguishing letter: Russian is the likelier default
    return "ru"


def search_config(lang: str | None) -> str:
    """Postgres text search configuration for a language code.

    Never interpolate the result into SQL - bind it and CAST(... AS regconfig).
    The whitelist here is what keeps that cast safe.
    """
    if not lang:
        return DEFAULT_CONFIG
    return _CONFIG_BY_LANG.get(lang.lower(), DEFAULT_CONFIG)
