"""Provider adapters: the request shape each API actually expects."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from zk2.llm.anthropic_provider import AnthropicProvider
from zk2.llm.base import Message
from zk2.llm.ollama_provider import OllamaProvider

pytestmark = pytest.mark.unit

MESSAGES = [
    Message(role="system", content="You are terse."),
    Message(role="user", content="What is alpha?"),
]


async def _collect(provider: Any, **kwargs: Any) -> list[Any]:
    return [chunk async for chunk in provider.complete(MESSAGES, **kwargs)]


# ─── Anthropic ────────────────────────────────────────────────────────


class _TextStream:
    """Async iterator over the streamed text parts."""

    def __init__(self, parts: list[str]) -> None:
        self._parts = parts

    def __aiter__(self) -> _TextStream:
        self._it = iter(self._parts)
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeAnthropicStream:
    def __init__(self, parts: list[str], usage: tuple[int, int]) -> None:
        self.text_stream = _TextStream(parts)
        self._usage = usage

    async def __aenter__(self) -> FakeAnthropicStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get_final_message(self) -> SimpleNamespace:
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=self._usage[0], output_tokens=self._usage[1]),
            stop_reason="end_turn",
            stop_details=None,
        )


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class FakeMessages:
        def stream(self, **kwargs: Any) -> FakeAnthropicStream:
            seen.update(kwargs)
            return FakeAnthropicStream(["Alpha ", "is a letter."], (120, 8))

    monkeypatch.setattr(
        "zk2.llm.anthropic_provider.AsyncAnthropic",
        lambda **_kwargs: SimpleNamespace(messages=FakeMessages()),
    )
    return seen


async def test_anthropic_lifts_the_system_prompt_out_of_messages(
    fake_anthropic: dict[str, Any],
) -> None:
    provider = AnthropicProvider(api_key="sk-test")
    chunks = await _collect(provider, model="claude-sonnet-5")

    assert fake_anthropic["system"] == "You are terse."
    assert [m["role"] for m in fake_anthropic["messages"]] == ["user"]
    assert "".join(c.delta for c in chunks) == "Alpha is a letter."
    assert chunks[-1].tokens_in == 120
    assert chunks[-1].tokens_out == 8


async def test_anthropic_omits_temperature_for_models_that_reject_it(
    fake_anthropic: dict[str, Any],
) -> None:
    """Sampling was removed on the current Claude models - sending it is a 400."""
    provider = AnthropicProvider(api_key="sk-test")
    await _collect(provider, model="claude-opus-5", temperature=0.7)
    assert "temperature" not in fake_anthropic


async def test_anthropic_sends_temperature_where_it_is_supported(
    fake_anthropic: dict[str, Any],
) -> None:
    provider = AnthropicProvider(api_key="sk-test")
    await _collect(provider, model="claude-haiku-4-5", temperature=0.7)
    assert fake_anthropic["temperature"] == 0.7


async def test_anthropic_always_sends_a_max_tokens_ceiling(
    fake_anthropic: dict[str, Any],
) -> None:
    provider = AnthropicProvider(api_key="sk-test")
    await _collect(provider, model="claude-sonnet-5")
    assert fake_anthropic["max_tokens"] > 0


async def test_anthropic_caps_max_tokens_at_the_catalog_limit(
    fake_anthropic: dict[str, Any],
) -> None:
    provider = AnthropicProvider(api_key="sk-test")
    await _collect(provider, model="claude-sonnet-5", max_tokens=10_000_000)
    assert fake_anthropic["max_tokens"] == 128000


def test_anthropic_costs_come_from_the_catalog() -> None:
    provider = AnthropicProvider(api_key="sk-test")
    # claude-sonnet-5: $2 in / $10 out per million
    assert provider.estimate_cost("claude-sonnet-5", tokens_in=1_000_000, tokens_out=0) == Decimal(
        "2.000000"
    )
    assert provider.estimate_cost("who-knows", tokens_in=10, tokens_out=10) is None


# ─── Ollama ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch):
    import httpx

    real_client = httpx.AsyncClient
    captured: dict[str, Any] = {}

    def _install(lines: list[dict[str, Any]]) -> dict[str, Any]:
        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            body = "\n".join(json.dumps(line) for line in lines)
            return httpx.Response(200, text=body)

        def _factory(**kwargs: object) -> httpx.AsyncClient:
            kwargs.pop("timeout", None)
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr("zk2.llm.ollama_provider.httpx.AsyncClient", _factory)
        return captured

    return _install


async def test_ollama_streams_ndjson(fake_ollama) -> None:
    captured = fake_ollama(
        [
            {"message": {"content": "Local "}, "done": False},
            {"message": {"content": "answer."}, "done": False},
            {"done": True, "prompt_eval_count": 42, "eval_count": 7, "done_reason": "stop"},
        ]
    )
    provider = OllamaProvider(base_url="http://ollama.internal:11434/")
    chunks = await _collect(provider, model="llama3.1:8b")

    assert captured["url"] == "http://ollama.internal:11434/api/chat"
    assert "".join(c.delta for c in chunks) == "Local answer."
    assert chunks[-1].tokens_in == 42
    assert chunks[-1].tokens_out == 7
    assert chunks[-1].finish_reason == "stop"


async def test_ollama_keeps_the_system_message_in_the_conversation(fake_ollama) -> None:
    """Unlike Anthropic, Ollama takes the system prompt as a message."""
    captured = fake_ollama([{"done": True}])
    provider = OllamaProvider(base_url="http://localhost:11434")
    await _collect(provider, model="llama3.1:8b")
    assert [m["role"] for m in captured["payload"]["messages"]] == ["system", "user"]


async def test_ollama_passes_generation_options(fake_ollama) -> None:
    captured = fake_ollama([{"done": True}])
    provider = OllamaProvider(base_url="http://localhost:11434")
    await _collect(provider, model="qwen2.5:7b", temperature=0.3, max_tokens=256)
    assert captured["payload"]["options"] == {"temperature": 0.3, "num_predict": 256}


async def test_ollama_survives_a_malformed_line(fake_ollama) -> None:
    fake_ollama([{"message": {"content": "ok"}, "done": False}, {"done": True}])
    provider = OllamaProvider(base_url="http://localhost:11434")
    chunks = await _collect(provider, model="llama3.1:8b")
    assert "".join(c.delta for c in chunks) == "ok"


def test_local_inference_is_free_not_unknown() -> None:
    provider = OllamaProvider(base_url="http://localhost:11434")
    assert provider.estimate_cost("llama3.1:8b", tokens_in=10_000, tokens_out=10_000) == Decimal(
        "0.000000"
    )
