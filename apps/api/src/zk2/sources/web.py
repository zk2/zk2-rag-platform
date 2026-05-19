"""Web scraping: fetch URL and extract title + main text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import structlog
from selectolax.parser import HTMLParser

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 15.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; zk2-chatbot/0.1; +https://github.com/zeka/zk2-chatbot)"
)


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str


async def fetch_url(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchedPage:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers=headers
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "").lower().split(";", 1)[0].strip()
        body = resp.content
        final = str(resp.url)

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
    text = (
        body_node.text(separator="\n", strip=True)
        if body_node
        else tree.text(strip=True)
    )
    return FetchedPage(url=url, final_url=final, title=title, text=text, content_type=ct)


async def discover_sitemap(base_url: str, *, limit: int = 500) -> list[str]:
    """Return unique URLs found in the site's sitemap.xml (or robots.txt → sitemaps)."""
    from usp.tree import sitemap_tree_for_homepage  # type: ignore[import-not-found]

    def _scan() -> list[str]:
        tree = sitemap_tree_for_homepage(base_url)
        urls: set[str] = set()
        for page in tree.all_pages():
            urls.add(page.url.rstrip("/"))
            if len(urls) >= limit:
                break
        return sorted(urls)

    return await asyncio.to_thread(_scan)
