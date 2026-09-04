"""Language detection and the text search configuration whitelist."""

from __future__ import annotations

import pytest

from zk2.sources.language import DEFAULT_CONFIG, detect_language, search_config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The quick brown fox jumps over the lazy dog", "en"),
        ("", "en"),
        ("12345 !@#$%", "en"),
        ("Договор аренды нежилого помещения заключается в письменной форме", "ru"),
        ("Це договір оренди нежитлового приміщення українською мовою", "uk"),
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_mixed_text_follows_the_dominant_script() -> None:
    mostly_english = "English sentence with one word: договор " * 10
    assert detect_language(mostly_english) == "en"


def test_russian_specific_letters_win_over_the_default() -> None:
    assert detect_language("Объём выборки был увеличен") == "ru"


@pytest.mark.parametrize(
    ("lang", "config"),
    [
        ("en", "english"),
        ("ru", "russian"),
        ("de", "german"),
        # Postgres ships no Ukrainian configuration; simple beats wrong stemming
        ("uk", "simple"),
        ("zz", "simple"),
        (None, "simple"),
        ("", "simple"),
    ],
)
def test_search_config(lang: str | None, config: str) -> None:
    assert search_config(lang) == config


def test_search_config_is_a_whitelist() -> None:
    """The result is cast to regconfig in SQL, so it must never echo input."""
    assert search_config("english'; DROP TABLE sources; --") == DEFAULT_CONFIG
