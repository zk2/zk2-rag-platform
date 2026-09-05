"""Security headers on every API response.

The API serves JSON, so its content policy can be as tight as policies get:
nothing may be loaded, framed or embedded. The browser app has its own headers
in the Next config - these protect the API's own responses, including the
error pages and the OpenAPI docs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from zk2.config import get_settings

Handler = Callable[[Request], Awaitable[Response]]

# The API returns JSON and nothing else: no scripts, no frames, no fetches
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# Swagger and Redoc need their own assets, so the docs get a narrower policy
DOCS_CSP = (
    "default-src 'none'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: "
    "https://fastapi.tiangolo.com; font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

HSTS = "max-age=31536000; includeSubDomains"


async def security_headers_middleware(request: Request, call_next: Handler) -> Response:
    response = await call_next(request)
    path = request.url.path

    response.headers.setdefault(
        "Content-Security-Policy",
        DOCS_CSP if path.startswith(DOCS_PATHS) else API_CSP,
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    # HSTS only where TLS is actually terminated in front of us
    if get_settings().is_prod:
        response.headers.setdefault("Strict-Transport-Security", HSTS)
    return response
