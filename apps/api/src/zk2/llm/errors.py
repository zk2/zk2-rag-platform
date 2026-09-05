"""One domain error for every third-party model provider failure.

Each SDK raises its own exception type, and their string forms are the raw HTTP
body - `Error code: 429 - {'error': {'message': 'You have no credits
remaining...'}}`. Two things have to come out of that: a sentence a person can
act on, and a status saying the failure came from upstream rather than from the
request. `ProviderError` carries both, so a dead key reads as "openai: You have
no credits remaining" instead of "Internal server error".

The extraction is deliberately duck-typed. OpenAI and Anthropic ship the same
generated client shape (`status_code` + `body`), google-genai exposes `code` and
`message`, and Ollama is plain httpx - one pass over those attributes covers all
four without importing any of the SDKs here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import status

from zk2.core.errors import AppError

# Substrings that separate "you are out of money" from "you are going too fast".
# Both arrive as 429, and the fix for them is not the same.
_QUOTA_MARKERS = ("quota", "credit", "billing", "insufficient", "payment")


class ProviderError(AppError):
    """A call to a third-party model provider failed.

    502 rather than the provider's own status: the client's request was fine,
    the dependency behind it was not.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        code: str | None = None,
        provider_status: int | None = None,
    ) -> None:
        super().__init__(f"{provider}: {message}", code=code)
        self.provider = provider
        self.provider_status = provider_status


def _from_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return str(error["message"])
    if isinstance(error, str):
        return error
    if isinstance(body.get("message"), str):
        return str(body["message"])
    return None


def _message_of(exc: Exception) -> str:
    from_body = _from_body(getattr(exc, "body", None))
    if from_body:
        return from_body

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            from_response = _from_body(response.json())
        except Exception:  # a body that is not JSON is not itself an error here
            from_response = None
        if from_response:
            return from_response

    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message
    # httpx connection errors are frequently empty; the class name is all there is
    return str(exc) or exc.__class__.__name__


def _status_of(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _code_for(http_status: int | None, message: str) -> str:
    if http_status in (401, 403):
        return "provider_auth"
    if http_status == 429:
        lowered = message.lower()
        if any(marker in lowered for marker in _QUOTA_MARKERS):
            return "provider_quota"
        return "provider_rate_limited"
    if http_status is None or http_status >= 500:
        return "provider_unavailable"
    return "provider_error"


def translate(provider: str, exc: Exception) -> ProviderError:
    """Wrap a provider SDK exception in the domain error."""
    message = _message_of(exc)
    http_status = _status_of(exc)
    return ProviderError(
        provider,
        message,
        code=_code_for(http_status, message),
        provider_status=http_status,
    )


@asynccontextmanager
async def provider_call(provider: str) -> AsyncIterator[None]:
    """Translate whatever the SDK raises inside this block.

    Domain errors pass through untouched - a ValidationError about a model's
    dimensions is ours, not the provider's. CancelledError and GeneratorExit
    derive from BaseException and are never caught here, so an abandoned stream
    still unwinds normally.
    """
    try:
        yield
    except AppError:
        raise
    except Exception as exc:
        raise translate(provider, exc) from exc
