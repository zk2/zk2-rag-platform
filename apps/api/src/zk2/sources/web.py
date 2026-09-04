"""Web scraping: fetch URL and extract title + main text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from selectolax.parser import HTMLParser

from zk2.config import get_settings
from zk2.core.net import assert_url_allowed, fetch_safely

logger = structlog.get_logger()


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str


async def fetch_url(url: str, *, timeout: float | None = None) -> FetchedPage:
    """Fetch a user-supplied URL. SSRF checks and the size cap live in core.net."""
    resp = await fetch_safely(url, timeout=timeout)
    ct = resp.content_type
    body = resp.content
    final = resp.final_url

    if "html" not in ct:
        # Plain text / non-HTML payloads
        text = body.decode("utf-8", errors="replace")
        return FetchedPage(url=url, final_url=final, title=url, text=text, content_type=ct)

    tree = HTMLParser(body.decode("utf-8", errors="replace"))
    for selector in ("script", "style", "noscript", "iframe", "header", "footer", "nav"):
        for node in tree.css(selector):
            node.decompose()

    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else url

    body_node = tree.body
    text = body_node.text(separator="\n", strip=True) if body_node else tree.text(strip=True)
    return FetchedPage(url=url, final_url=final, title=title, text=text, content_type=ct)


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
