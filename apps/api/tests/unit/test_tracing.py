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
    def __init__(self, name: str = "root") -> None:
        self.name = name
        self.updates: list[dict[str, Any]] = []
        self.children: list[FakeObservation] = []
        self.ended = False
        self.trace_id = "trace-123"

    def start_observation(self, **kwargs: Any) -> FakeObservation:
        child = FakeObservation(str(kwargs.get("name", "")))
        self.children.append(child)
        return child

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)

    def end(self) -> None:
        self.ended = True


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[FakeObservation] = []
        self.flushed = False

    def start_observation(self, **kwargs: Any) -> FakeObservation:
        observation = FakeObservation(str(kwargs.get("name", "")))
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
    (root,) = fake_client.observations
    (generation,) = root.children
    assert root.ended and generation.ended
    assert generation.updates[0]["output"] == "answer"


async def test_a_step_is_a_child_of_the_turn_and_not_a_second_root(
    fake_client: FakeLangfuse,
) -> None:
    """One turn is one tree.

    Starting an observation from the client attaches it to whatever OTel
    context is current - for a chat turn that is the websocket connection, so
    every span arrived as a root of its own and one turn showed up as eight
    unrelated traces.
    """
    turn = start_turn("rag.turn")
    node = turn.step("answer")
    node.child("generation", kind="generation")

    assert len(fake_client.observations) == 1, "only the turn talks to the client"
    (root,) = fake_client.observations
    assert [c.name for c in root.children] == ["answer"]
    assert [c.name for c in root.children[0].children] == ["generation"]


async def test_a_parent_that_fails_to_open_a_child_still_returns_a_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenChild(FakeObservation):
        def start_observation(self, **_kwargs: Any) -> FakeObservation:
            raise RuntimeError("langfuse went away mid-turn")

    class Client(FakeLangfuse):
        def start_observation(self, **kwargs: Any) -> FakeObservation:
            observation = BrokenChild(str(kwargs.get("name", "")))
            self.observations.append(observation)
            return observation

    monkeypatch.setattr(tracing, "get_langfuse", Client)
    turn = start_turn("rag.turn")
    step = turn.step("answer")
    step.end(duration_ms=1)
    turn.end()


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


def test_the_sampler_keeps_a_request_and_drops_the_plumbing() -> None:
    """A trace should describe something somebody asked for.

    Health checks, metric scrapes and a parentless Redis call are machinery on
    a timer: two hundred identical traces an hour, and the real requests
    nowhere in the first page of results.
    """
    from opentelemetry.sdk.trace.sampling import Decision
    from opentelemetry.trace import SpanKind

    from zk2.core.telemetry import _DropPlumbing

    sampler = _DropPlumbing()

    def decide(**kwargs: Any) -> Decision:
        return sampler.should_sample(None, 1, kwargs.pop("name", "span"), **kwargs).decision

    assert decide(kind=SpanKind.SERVER, attributes={"url.path": "/health"}) is Decision.DROP
    assert decide(kind=SpanKind.SERVER, attributes={"url.path": "/metrics"}) is Decision.DROP
    assert decide(name="LLEN", kind=SpanKind.CLIENT) is Decision.DROP
    assert (
        decide(kind=SpanKind.SERVER, attributes={"url.path": "/bots/1/chat"})
        is Decision.RECORD_AND_SAMPLE
    )
    # The turn's own span: internal, no attributes, and the thing we came for
    assert decide(name="rag.turn", kind=SpanKind.INTERNAL) is Decision.RECORD_AND_SAMPLE
