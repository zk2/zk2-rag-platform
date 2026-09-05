"""Provider SDK failures become one readable domain error."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from zk2.core.errors import ValidationError
from zk2.llm.errors import ProviderError, provider_call, translate

pytestmark = pytest.mark.unit


class FakeStatusError(Exception):
    """The shape OpenAI and Anthropic both raise: a status plus the parsed body."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        super().__init__(f"Error code: {status_code} - {body}")
        self.status_code = status_code
        self.body = body


def _openai_error(status_code: int, message: str) -> FakeStatusError:
    return FakeStatusError(status_code, {"error": {"message": message, "type": "invalid_request"}})


def test_the_provider_message_replaces_the_raw_body() -> None:
    exc = _openai_error(429, "You have no credits remaining. Add credits to continue.")
    error = translate("openai", exc)

    assert isinstance(error, ProviderError)
    assert error.message == "openai: You have no credits remaining. Add credits to continue."
    assert "Error code:" not in error.message
    assert error.status_code == 502, "the caller's request was fine; the dependency was not"


@pytest.mark.parametrize(
    ("status_code", "message", "expected"),
    [
        (401, "Incorrect API key provided", "provider_auth"),
        (403, "Country not supported", "provider_auth"),
        (429, "You have no credits remaining", "provider_quota"),
        (429, "Rate limit reached for gpt-4.1-mini", "provider_rate_limited"),
        (503, "The engine is currently overloaded", "provider_unavailable"),
        (400, "max_tokens is too large", "provider_error"),
    ],
)
def test_the_code_says_which_kind_of_failure_it_was(
    status_code: int, message: str, expected: str
) -> None:
    """An exhausted balance and a burst of traffic both arrive as 429, and the
    fix for them is not the same."""
    assert translate("openai", _openai_error(status_code, message)).code == expected


def test_a_connection_failure_has_no_status_and_reads_as_unavailable() -> None:
    error = translate("ollama", ConnectionError())

    assert error.code == "provider_unavailable"
    assert error.provider_status is None
    assert error.message == "ollama: ConnectionError", "an empty exception still needs a name"


def test_an_httpx_style_error_is_read_from_the_response_body() -> None:
    response = SimpleNamespace(status_code=404, json=lambda: {"error": "model not found"})
    error = translate("ollama", SimpleNamespace(response=response))  # type: ignore[arg-type]

    assert error.message == "ollama: model not found"
    assert error.provider_status == 404


def test_a_body_that_is_not_json_does_not_hide_the_failure() -> None:
    def _explode() -> dict[str, Any]:
        raise ValueError("not json")

    exc = FakeStatusError(502, {})
    exc.body = None  # type: ignore[assignment]
    exc.response = SimpleNamespace(status_code=502, json=_explode)  # type: ignore[attr-defined]

    error = translate("gemini", exc)
    assert error.code == "provider_unavailable"
    assert "502" in error.message, "an unparseable body still reports what the SDK said"


async def test_domain_errors_pass_through_untranslated() -> None:
    """A ValidationError about a model's dimensions is ours, not the provider's."""
    with pytest.raises(ValidationError):
        async with provider_call("openai"):
            raise ValidationError("width mismatch")


async def test_base_exceptions_are_not_swallowed() -> None:
    """Cancelling a stream must unwind, not turn into a provider outage."""
    with pytest.raises(KeyboardInterrupt):
        async with provider_call("openai"):
            raise KeyboardInterrupt
