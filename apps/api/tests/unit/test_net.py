"""SSRF guard: which destinations we refuse to fetch."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

import pytest

from zk2.core.net import UnsafeUrlError, _is_blocked_address, assert_url_allowed

pytestmark = pytest.mark.unit

BLOCKED_ADDRESSES = [
    "127.0.0.1",  # loopback
    "10.1.2.3",  # RFC1918
    "172.16.5.4",  # RFC1918
    "192.168.0.10",  # RFC1918
    "169.254.169.254",  # cloud metadata - the one that matters
    "0.0.0.0",  # unspecified
    "224.0.0.1",  # multicast
    "::1",  # IPv6 loopback
    "fe80::1",  # IPv6 link-local
    "fc00::1",  # IPv6 unique-local
    "::ffff:169.254.169.254",  # IPv4-mapped metadata address
]

PUBLIC_ADDRESSES = ["8.8.8.8", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
def test_blocked_addresses(address: str) -> None:
    assert _is_blocked_address(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", PUBLIC_ADDRESSES)
def test_public_addresses_allowed(address: str) -> None:
    assert _is_blocked_address(ipaddress.ip_address(address)) is False


@pytest.fixture
def resolves_to(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], None]:
    """Pin DNS resolution so the tests never touch a real resolver."""

    def _install(addresses: list[str]) -> None:
        async def _fake_resolve(host: str, port: int) -> list[str]:
            return addresses

        monkeypatch.setattr("zk2.core.net._resolve", _fake_resolve)

    return _install


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "not-a-url",
    ],
)
async def test_non_http_schemes_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await assert_url_allowed(url)


async def test_url_without_host_rejected() -> None:
    with pytest.raises(UnsafeUrlError):
        await assert_url_allowed("http://")


async def test_metadata_endpoint_rejected(resolves_to: Callable[[list[str]], None]) -> None:
    resolves_to(["169.254.169.254"])
    with pytest.raises(UnsafeUrlError, match="non-public"):
        await assert_url_allowed("http://metadata.evil.example/latest/meta-data/")


async def test_localhost_rejected(resolves_to: Callable[[list[str]], None]) -> None:
    resolves_to(["127.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        await assert_url_allowed("http://localhost:8000/admin")


async def test_any_private_answer_rejects(resolves_to: Callable[[list[str]], None]) -> None:
    """A host that resolves to both a public and a private address is refused."""
    resolves_to(["93.184.216.34", "10.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        await assert_url_allowed("http://split-horizon.example/")


async def test_public_host_allowed(resolves_to: Callable[[list[str]], None]) -> None:
    resolves_to(["93.184.216.34"])
    await assert_url_allowed("https://example.com/page")


async def test_unresolvable_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import socket

    loop = asyncio.get_running_loop()

    async def _boom(*_args: object, **_kwargs: object) -> list[object]:
        raise socket.gaierror("nope")

    monkeypatch.setattr(loop, "getaddrinfo", _boom)
    with pytest.raises(UnsafeUrlError, match="Cannot resolve"):
        await assert_url_allowed("http://does-not-exist.example/")


# ─── fetch_safely: redirects, size cap, content type ──────────────────


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch, resolves_to: Callable[[list[str]], None]):
    """Serve canned responses through httpx's MockTransport, DNS pinned to public."""
    import httpx

    resolves_to(["93.184.216.34"])
    real_client = httpx.AsyncClient

    def _install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        def _factory(**kwargs: object) -> httpx.AsyncClient:
            kwargs.pop("follow_redirects", None)
            return real_client(
                transport=httpx.MockTransport(handler), follow_redirects=False, **kwargs
            )

        monkeypatch.setattr("zk2.core.net.httpx.AsyncClient", _factory)

    return _install


async def test_fetch_returns_body_and_content_type(mock_http) -> None:
    import httpx

    from zk2.core.net import fetch_safely

    mock_http(
        lambda request: httpx.Response(
            200, text="<html>hi</html>", headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    result = await fetch_safely("https://example.com/page")
    assert result.content == b"<html>hi</html>"
    assert result.content_type == "text/html"
    assert result.status_code == 200


async def test_fetch_stops_at_size_cap(mock_http) -> None:
    import httpx

    from zk2.core.errors import PayloadTooLargeError
    from zk2.core.net import fetch_safely

    mock_http(lambda request: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(PayloadTooLargeError):
        await fetch_safely("https://example.com/big", max_bytes=1000)


async def test_redirect_to_private_address_is_refused(
    mock_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classic bypass: a public URL that 302s to the metadata service."""
    import httpx

    from zk2.core.net import UnsafeUrlError, fetch_safely

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
        return httpx.Response(200, content=b"secrets")

    mock_http(handler)

    async def _resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"] if host == "example.com" else ["169.254.169.254"]

    monkeypatch.setattr("zk2.core.net._resolve", _resolve)

    with pytest.raises(UnsafeUrlError):
        await fetch_safely("https://example.com/redirect")


async def test_redirect_chain_is_bounded(mock_http) -> None:
    import httpx

    from zk2.core.net import UnsafeUrlError, fetch_safely

    mock_http(lambda request: httpx.Response(302, headers={"location": "https://example.com/loop"}))
    with pytest.raises(UnsafeUrlError, match="Too many redirects"):
        await fetch_safely("https://example.com/loop")


async def test_redirect_without_location_is_refused(mock_http) -> None:
    import httpx

    from zk2.core.net import UnsafeUrlError, fetch_safely

    mock_http(lambda request: httpx.Response(302))
    with pytest.raises(UnsafeUrlError, match="Location"):
        await fetch_safely("https://example.com/broken")


async def test_http_error_propagates(mock_http) -> None:
    import httpx

    from zk2.core.net import fetch_safely

    mock_http(lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_safely("https://example.com/missing")
