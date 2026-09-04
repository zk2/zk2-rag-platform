"""Tracing must be invisible when it is not configured, and never fatal."""

from __future__ import annotations

from typing import Any

import pytest

from zk2.core import tracing
from zk2.core.tracing import flush_traces, get_langfuse, start_turn

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_client_cache() -> None:
    get_langfuse.cache_clear()


async def test_disabled_tracing_still_returns_a_usable_turn() -> None:
    """No keys configured: every call is a no-op, nothing raises."""
    turn = start_turn("rag.turn", metadata={"org_id": 1})
    step = turn.step("retrieval", kind="retriever", input_data="query")
    step.end(output=[], chunks=0)
    turn.end(output={"citations": 0})
    assert turn.trace_url is None
    assert turn.trace_id is None


async def test_no_client_when_keys_are_absent() -> None:
    assert get_langfuse() is None


async def test_flush_without_a_client_is_a_no_op() -> None:
    await flush_traces()


class FakeObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.ended = False
        self.trace_id = "trace-123"

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)

    def end(self) -> None:
        self.ended = True


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[FakeObservation] = []
        self.flushed = False

    def start_observation(self, **_kwargs: Any) -> FakeObservation:
        observation = FakeObservation()
        self.observations.append(observation)
        return observation

    def get_current_trace_id(self) -> str:
        return "trace-123"

    def get_trace_url(self, *, trace_id: str) -> str:
        return f"https://langfuse.example/traces/{trace_id}"

    def flush(self) -> None:
        self.flushed = True


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeLangfuse:
    client = FakeLangfuse()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: client)
    return client


async def test_turn_and_steps_reach_the_client(fake_client: FakeLangfuse) -> None:
    turn = start_turn("rag.turn", metadata={"org_id": 1})
    step = turn.step("generation", kind="generation", model="gpt-4o-mini")
    step.end(output="answer", usage_details={"input": 10, "output": 5})
    turn.end(output={"citations": 1})

    assert turn.trace_url == "https://langfuse.example/traces/trace-123"
    assert len(fake_client.observations) == 2
    assert all(o.ended for o in fake_client.observations)
    assert fake_client.observations[1].updates[0]["output"] == "answer"


async def test_a_broken_client_does_not_break_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observability going down must not take the chat path with it."""

    class BrokenLangfuse:
        def start_observation(self, **_kwargs: Any) -> Any:
            raise RuntimeError("langfuse is unreachable")

        def get_current_trace_id(self) -> str:
            raise RuntimeError("nope")

        def get_trace_url(self, **_kwargs: Any) -> str:
            raise RuntimeError("nope")

    monkeypatch.setattr(tracing, "get_langfuse", BrokenLangfuse)
    turn = start_turn("rag.turn")
    step = turn.step("retrieval")
    step.end(chunks=3)
    turn.end()
    assert turn.trace_url is None


async def test_flush_reaches_the_client(fake_client: FakeLangfuse) -> None:
    await flush_traces()
    assert fake_client.flushed is True
