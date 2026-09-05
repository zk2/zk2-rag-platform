"""The agent loop and MCP tools, exercised without touching a provider."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from mcp.server.mcpserver import MCPServer
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.agents.graph import AgentOutcome, stream_agent, to_langchain_tools
from zk2.agents.mcp_client import check_server, list_server_tools, mcp_tools_for_org
from zk2.agents.models import McpServer
from zk2.agents.registry import tools_for_org
from zk2.agents.tools import builtin_tools
from zk2.core.tracing import start_turn

pytestmark = pytest.mark.integration


class FakeToolCallingModel(BaseChatModel):
    """Replays prepared AIMessages and accepts bind_tools, like a real one."""

    # pydantic fields, not class state: pydantic copies these per instance
    responses: list[AIMessage] = []  # noqa: RUF012
    index: int = 0
    bound_tools: list[Any] = []  # noqa: RUF012

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def bind_tools(self, tools: Sequence[Any], **_kwargs: Any) -> FakeToolCallingModel:
        self.bound_tools = list(tools)
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **_: Any
    ) -> ChatResult:
        message = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **_: Any
    ) -> ChatResult:
        return self._generate(messages)


def _calls_then_answers(tool_name: str, args: dict[str, Any]) -> FakeToolCallingModel:
    return FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": tool_name, "args": args, "id": "call_1"}],
                usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            ),
            AIMessage(
                content="It is 42.",
                usage_metadata={"input_tokens": 150, "output_tokens": 8, "total_tokens": 158},
            ),
        ]
    )


async def _run(model: BaseChatModel, tools: Any, question: str = "6*7?"):
    events = []
    outcome: AgentOutcome | None = None
    async for item in stream_agent(
        chat_model=model,
        tools=tools,
        system_prompt="You are terse.",
        question=question,
        provider="openai",
        model="gpt-4.1-mini",
        max_steps=5,
        trace=start_turn("test.agent"),
    ):
        if isinstance(item, AgentOutcome):
            outcome = item
        else:
            events.append(item)
    assert outcome is not None
    return events, outcome


async def test_tool_call_and_result_are_streamed() -> None:
    tools = [t for t in builtin_tools() if t.name == "calculator"]
    events, outcome = await _run(_calls_then_answers("calculator", {"expression": "6*7"}), tools)

    kinds = [e.kind for e in events]
    assert kinds == ["tool_call", "tool_result", "token"]
    assert events[0].payload["name"] == "calculator"
    assert "42" in events[1].payload["result"]
    assert outcome.answer == "It is 42."


async def test_usage_and_cost_survive_the_framework() -> None:
    """LangGraph runs the loop; the catalog still prices it."""
    tools = [t for t in builtin_tools() if t.name == "calculator"]
    _, outcome = await _run(_calls_then_answers("calculator", {"expression": "6*7"}), tools)

    assert outcome.tokens_in == 250
    assert outcome.tokens_out == 18
    # gpt-4.1-mini: $0.40 in / $1.60 out per million
    assert outcome.cost_usd == Decimal("0.000129")


async def test_tool_failure_is_reported_to_the_model_not_raised() -> None:
    tools = [t for t in builtin_tools() if t.name == "calculator"]
    events, outcome = await _run(
        _calls_then_answers("calculator", {"expression": "__import__('os')"}), tools
    )
    assert [e.kind for e in events] == ["tool_call", "tool_result", "token"]
    assert "Only arithmetic" in events[1].payload["result"]
    assert outcome.failed is False


async def test_the_model_is_offered_exactly_the_selected_tools() -> None:
    tools = [t for t in builtin_tools() if t.name in {"calculator", "get_current_time"}]
    model = _calls_then_answers("calculator", {"expression": "1+1"})
    await _run(model, tools)
    assert {t.name for t in model.bound_tools} == {"calculator", "get_current_time"}


async def test_langchain_tools_carry_the_schema() -> None:
    converted = to_langchain_tools(builtin_tools())
    by_name = {t.name: t for t in converted}
    assert "calculator" in by_name
    assert by_name["calculator"].description


# ─── MCP ──────────────────────────────────────────────────────────────


def _memory_server() -> MCPServer:
    server = MCPServer("probe")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    return server


@pytest.fixture
def in_memory_mcp(monkeypatch: pytest.MonkeyPatch) -> MCPServer:
    """Point the MCP client at an in-process server instead of a URL."""
    import mcp

    server = _memory_server()
    real_client = mcp.Client
    monkeypatch.setattr(mcp, "Client", lambda *_args, **kwargs: real_client(server, **kwargs))

    # The SSRF guard resolves the URL for real; these tests use a placeholder
    # host and cover the guard separately, below.
    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("zk2.agents.mcp_client.assert_url_allowed", _allow)
    return server


async def test_mcp_tools_are_listed_and_namespaced(
    db: AsyncSession, org_owner: dict[str, Any], in_memory_mcp: MCPServer
) -> None:
    server = McpServer(org_id=org_owner["org_id"], name="probe", url="https://mcp.example/mcp")
    specs = await list_server_tools(server)

    assert [s.name for s in specs] == ["probe__add"]
    assert specs[0].kind == "mcp"
    assert specs[0].args_model["properties"]["a"]["type"] == "integer"


async def test_an_mcp_tool_can_be_called(
    db: AsyncSession, org_owner: dict[str, Any], in_memory_mcp: MCPServer
) -> None:
    server = McpServer(org_id=org_owner["org_id"], name="probe", url="https://mcp.example/mcp")
    (spec,) = await list_server_tools(server)
    assert await spec.handler(a=2, b=3) == "5"


async def test_mcp_tools_join_the_builtin_registry(
    db: AsyncSession, org_owner: dict[str, Any], in_memory_mcp: MCPServer
) -> None:
    db.add(McpServer(org_id=org_owner["org_id"], name="probe", url="https://mcp.example/mcp"))
    await db.commit()

    names = {spec.name for spec in await tools_for_org(db, org_id=org_owner["org_id"])}
    assert "probe__add" in names
    assert "calculator" in names


async def test_a_broken_server_costs_only_its_own_tools(
    db: AsyncSession, org_owner: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An MCP server that is down must not fail the agent turn."""
    import mcp

    def _explode(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mcp, "Client", _explode)
    db.add(McpServer(org_id=org_owner["org_id"], name="dead", url="https://down.example/mcp"))
    await db.commit()

    names = {spec.name for spec in await tools_for_org(db, org_id=org_owner["org_id"])}
    assert "calculator" in names
    assert not any(name.startswith("dead__") for name in names)


async def test_disabled_servers_are_skipped(
    db: AsyncSession, org_owner: dict[str, Any], in_memory_mcp: MCPServer
) -> None:
    db.add(
        McpServer(
            org_id=org_owner["org_id"],
            name="probe",
            url="https://mcp.example/mcp",
            enabled=False,
        )
    )
    await db.commit()
    assert await mcp_tools_for_org(db, org_id=org_owner["org_id"]) == []


async def test_health_check_records_status_and_tool_count(
    db: AsyncSession, org_owner: dict[str, Any], in_memory_mcp: MCPServer
) -> None:
    server = McpServer(org_id=org_owner["org_id"], name="probe", url="https://mcp.example/mcp")
    db.add(server)
    await db.flush()

    await check_server(db, server)
    assert server.status == "ok"
    assert server.tool_count == 1
    assert server.last_checked_at is not None


async def test_health_check_records_an_unreachable_server(
    db: AsyncSession, org_owner: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server that refuses connections is marked, not retried into the turn."""
    import mcp

    async def _allow(_url: str) -> None:
        return None

    def _explode(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr("zk2.agents.mcp_client.assert_url_allowed", _allow)
    monkeypatch.setattr(mcp, "Client", _explode)
    server = McpServer(org_id=org_owner["org_id"], name="dead", url="https://example.com/mcp")
    db.add(server)
    await db.flush()

    await check_server(db, server)
    assert server.status == "error"
    assert server.last_error and "connection refused" in server.last_error
    assert server.tool_count is None


async def test_health_check_records_a_bad_hostname(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """A typo in the URL surfaces on the settings page, not at chat time."""
    server = McpServer(org_id=org_owner["org_id"], name="typo", url="https://nope.invalid/mcp")
    db.add(server)
    await db.flush()

    await check_server(db, server)
    assert server.status == "error"
    assert server.last_error


async def test_mcp_urls_get_the_ssrf_guard(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    """A server URL is user input like any other."""
    from zk2.core.net import UnsafeUrlError

    server = McpServer(org_id=org_owner["org_id"], name="local", url="http://127.0.0.1:9000/mcp")
    with pytest.raises(UnsafeUrlError):
        await list_server_tools(server)
