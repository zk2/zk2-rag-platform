"""Outbound HTTP for user-supplied URLs.

Every URL handled here arrived from a user (a source URL, a sitemap, later an
agent tool), so it is treated as hostile:

* only http/https - no file://, gopher://, data: and friends
* the hostname is resolved before the request and every resolved address is
  checked against loopback, private, link-local (cloud metadata!), multicast
  and reserved ranges
* redirects are followed manually so each hop is validated the same way
* the response body is capped, so a multi-gigabyte URL cannot exhaust memory

Residual risk: DNS rebinding between our resolution and httpx's own. Closing it
means pinning the connection to the validated address; the plan tracks that for
the hardening slice (week 9), where an egress proxy is the better answer anyway.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import structlog

from zk2.config import get_settings
from zk2.core.errors import PayloadTooLargeError, ValidationError

logger = structlog.get_logger()

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; zk2-chatbot/0.1; +https://github.com/zeka/zk2-chatbot)"
)


class UnsafeUrlError(ValidationError):
    """The URL points somewhere we refuse to fetch from."""

    code = "unsafe_url"


@dataclass(slots=True)
class SafeResponse:
    url: str
    final_url: str
    status_code: int
    content: bytes
    content_type: str


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # IPv4-mapped IPv6 (::ffff:169.254.169.254) must be judged by the mapped address
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped is not None and _is_blocked_address(mapped))


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Cannot resolve host: {host}") from exc
    return [str(info[4][0]) for info in infos]


async def assert_url_allowed(url: str) -> None:
    """Raise UnsafeUrlError unless the URL is safe to fetch."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Unsupported URL scheme: {parts.scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")

    if get_settings().ingest.allow_private_networks:
        return

    port = parts.port or (443 if parts.scheme == "https" else 80)
    for address in await _resolve(host, port):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - getaddrinfo always returns literals
            raise UnsafeUrlError(f"Cannot parse resolved address for {host}") from None
        if _is_blocked_address(ip):
            logger.warning("net.blocked_url", host=host, address=address)
            raise UnsafeUrlError(f"Refusing to fetch a non-public address: {host} -> {address}")


async def _read_capped(response: httpx.Response, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(f"Response exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_safely(
    url: str,
    *,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> SafeResponse:
    """GET a user-supplied URL with SSRF and size protection."""
    settings = get_settings().ingest
    max_bytes = settings.max_fetch_bytes if max_bytes is None else max_bytes
    timeout = settings.fetch_timeout_seconds if timeout is None else timeout

    current = url
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=headers
    ) as client:
        for _hop in range(settings.max_redirects + 1):
            await assert_url_allowed(current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("Redirect without a Location header")
                    current = str(response.url.join(location))
                    continue
                response.raise_for_status()
                content = await _read_capped(response, max_bytes=max_bytes)
                content_type = (
                    response.headers.get("content-type", "").lower().split(";", 1)[0].strip()
                )
                return SafeResponse(
                    url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    content=content,
                    content_type=content_type,
                )
    raise UnsafeUrlError(f"Too many redirects (> {settings.max_redirects})")
