"""The agent loop.

LangGraph owns the cycle - model, tools, model again - and this module owns
everything the framework does not know about: which tools exist for this
organization, how a tool call reaches the browser, and how tokens and cost get
counted. Usage is read back off the LangChain messages and pushed through the
same catalog and metrics as a plain completion, so an agent turn is billed and
measured exactly like a non-agent one.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from zk2.agents.tools import ToolSpec
from zk2.chat.events import StreamEvent
from zk2.core.metrics import llm_errors_total, record_llm_call
from zk2.core.tracing import TurnTrace
from zk2.llm.catalog import estimate_cost

logger = structlog.get_logger()

MAX_TOOL_RESULT_PREVIEW = 400


@dataclass(slots=True)
class AgentOutcome:
    """What the loop produced, for the caller to persist."""

    answer: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None
    steps: int = 0
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    failed: bool = False
    error: str | None = None


def to_langchain_tools(specs: Sequence[ToolSpec]) -> list[StructuredTool]:
    """Adapt our tool descriptions to what LangGraph expects."""
    return [
        StructuredTool.from_function(
            coroutine=spec.handler,
            name=spec.name,
            description=spec.description,
            args_schema=spec.args_model,
        )
        for spec in specs
    ]


def _preview(content: object) -> str:
    text = content if isinstance(content, str) else str(content)
    return text[:MAX_TOOL_RESULT_PREVIEW]


async def stream_agent(
    *,
    chat_model: BaseChatModel,
    tools: Sequence[ToolSpec],
    system_prompt: str,
    question: str,
    provider: str,
    model: str,
    max_steps: int,
    trace: TurnTrace,
) -> AsyncIterator[StreamEvent | AgentOutcome]:
    """Run the loop, yielding protocol events and finally the outcome."""
    from langchain.agents import create_agent  # noqa: PLC0415  (heavy import)

    outcome = AgentOutcome()
    agent = create_agent(chat_model, to_langchain_tools(tools), system_prompt=system_prompt)
    step = trace.step(
        "agent",
        kind="agent",
        input_data=question,
        model=model,
        metadata={"provider": provider, "tools": [t.name for t in tools]},
    )
    started = time.perf_counter()

    try:
        async for chunk in agent.astream(
            {"messages": [HumanMessage(question)]},
            stream_mode="updates",
            config={"recursion_limit": max_steps * 2},
        ):
            for node, payload in chunk.items():
                messages = payload.get("messages", []) if isinstance(payload, dict) else []
                for message in messages:
                    if isinstance(message, AIMessage):
                        usage = message.usage_metadata
                        if usage is not None:
                            outcome.tokens_in += usage.get("input_tokens") or 0
                            outcome.tokens_out += usage.get("output_tokens") or 0
                        for call in message.tool_calls or []:
                            outcome.steps += 1
                            outcome.tool_calls.append({"name": call["name"], "args": call["args"]})
                            yield StreamEvent(
                                "tool_call", {"name": call["name"], "args": call["args"]}
                            )
                        if message.content and not message.tool_calls:
                            outcome.answer = (
                                message.content
                                if isinstance(message.content, str)
                                else str(message.content)
                            )
                    elif isinstance(message, ToolMessage):
                        yield StreamEvent(
                            "tool_result",
                            {
                                "name": message.name or "tool",
                                "result": _preview(message.content),
                                "status": getattr(message, "status", "success"),
                            },
                        )
                    logger.debug("agent.message", node=node, type=type(message).__name__)
    except Exception as exc:
        llm_errors_total.labels(provider=provider, model=model).inc()
        outcome.failed = True
        outcome.error = str(exc)
        step.end(level="ERROR", status_message=str(exc)[:500])
        logger.exception("agent.failed")
        yield StreamEvent("error", {"message": str(exc)})
        yield outcome
        return

    duration = time.perf_counter() - started
    outcome.cost_usd = estimate_cost(
        model, tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out
    )
    record_llm_call(
        provider=provider,
        model=model,
        duration_seconds=duration,
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        cost_usd=float(outcome.cost_usd) if outcome.cost_usd is not None else None,
    )
    step.end(
        output=outcome.answer,
        usage_details={"input": outcome.tokens_in, "output": outcome.tokens_out},
        cost_details={"total": float(outcome.cost_usd)} if outcome.cost_usd is not None else None,
        tool_calls=len(outcome.tool_calls),
    )
    if outcome.answer:
        # The answer arrives whole from the loop rather than token by token
        yield StreamEvent("token", {"delta": outcome.answer})
    yield outcome
