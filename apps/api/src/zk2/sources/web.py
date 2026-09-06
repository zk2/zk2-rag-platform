"""Web scraping: fetch URL and extract title + main text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from zk2.config import get_settings
from zk2.core.net import assert_url_allowed, fetch_safely
from zk2.sources.loaders import parse_html
from zk2.sources.parsed import ParsedDocument, Segment
from zk2.sources.text_clean import clean_text

logger = structlog.get_logger()


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    document: ParsedDocument
    content_type: str

    @property
    def text(self) -> str:
        """The page as plain text, for callers that want no structure."""
        return self.document.text


async def fetch_url(url: str, *, timeout: float | None = None) -> FetchedPage:
    """Fetch a user-supplied URL. SSRF checks and the size cap live in core.net."""
    resp = await fetch_safely(url, timeout=timeout)
    ct = resp.content_type
    body = resp.content
    final = resp.final_url

    if "html" not in ct:
        # Plain text / non-HTML payloads
        text = clean_text(body.decode("utf-8", errors="replace"))
        return FetchedPage(
            url=url,
            final_url=final,
            title=url,
            document=ParsedDocument(segments=[Segment(text=text)] if text else []),
            content_type=ct,
        )

    document, title = parse_html(body.decode("utf-8", errors="replace"))
    return FetchedPage(
        url=url,
        final_url=final,
        title=title or url,
        document=document,
        content_type=ct,
    )


async def discover_sitemap(base_url: str, *, limit: int | None = None) -> list[str]:
    """Return unique URLs found in the site's sitemap.xml (or robots.txt -> sitemaps).

    The base URL is validated before `usp` starts fetching. Every discovered URL
    is validated again when it is actually fetched by `fetch_url`.
    """
    await assert_url_allowed(base_url)
    limit = get_settings().ingest.sitemap_default_limit if limit is None else limit
    from usp.tree import sitemap_tree_for_homepage  # noqa: PLC0415  (slow import)

    def _scan() -> list[str]:
        tree = sitemap_tree_for_homepage(base_url)
        urls: set[str] = set()
        for page in tree.all_pages():
            urls.add(page.url.rstrip("/"))
            if len(urls) >= limit:
                break
        return sorted(urls)

    return await asyncio.to_thread(_scan)
