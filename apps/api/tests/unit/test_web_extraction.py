"""Web page extraction on top of the SSRF-safe fetcher."""

from __future__ import annotations

import pytest

from zk2.core.net import SafeResponse
from zk2.sources.web import fetch_url

pytestmark = pytest.mark.unit


def _response(body: bytes, content_type: str = "text/html") -> SafeResponse:
    return SafeResponse(
        url="https://example.com/a",
        final_url="https://example.com/b",
        status_code=200,
        content=body,
        content_type=content_type,
    )


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch):
    def _install(response: SafeResponse) -> None:
        async def _fetch(*_args: object, **_kwargs: object) -> SafeResponse:
            return response

        monkeypatch.setattr("zk2.sources.web.fetch_safely", _fetch)

    return _install


async def test_extracts_title_and_visible_text(served) -> None:
    served(
        _response(
            b"<html><head><title>Doc title</title></head>"
            b"<body><nav>menu</nav><p>Body text</p>"
            b"<script>evil()</script><footer>legal</footer></body></html>"
        )
    )
    page = await fetch_url("https://example.com/a")
    assert page.title == "Doc title"
    assert "Body text" in page.text
    assert "evil()" not in page.text
    assert "menu" not in page.text
    assert "legal" not in page.text
    assert page.final_url == "https://example.com/b"


async def test_plain_text_passes_through(served) -> None:
    served(_response(b"just text", content_type="text/plain"))
    page = await fetch_url("https://example.com/a")
    assert page.text == "just text"
    assert page.title == "https://example.com/a"


async def test_page_without_title_falls_back_to_url(served) -> None:
    served(_response(b"<html><body><p>No title here</p></body></html>"))
    page = await fetch_url("https://example.com/a")
    assert page.title == "https://example.com/a"
    assert "No title here" in page.text
