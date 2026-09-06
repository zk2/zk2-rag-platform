"""What flows between pipeline nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from zk2.bots.models import Bot, BotVersion
from zk2.core.tracing import TraceStep, TurnTrace
from zk2.retrieval.base import RetrievedChunk


@dataclass(slots=True)
class PipelineState:
    """Mutable state for one turn. Nodes read what they need and add their own."""

    query: str
    source_ids: list[int]
    # Retrieval results keyed by the node that produced them
    candidates: dict[str, list[RetrievedChunk]] = field(default_factory=dict)
    selected: list[RetrievedChunk] = field(default_factory=list)
    context_parts: list[str] = field(default_factory=list)
    context_chunks: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None
    latency_ms: int = 0
    failed: bool = False


@dataclass(slots=True)
class NodeContext:
    """Everything a node may reach for, passed explicitly rather than imported."""

    db: AsyncSession
    org_id: int
    bot: Bot
    version: BotVersion
    trace: TurnTrace
    # The span of the node currently running, so a node can nest its own work
    # (a model call) inside it instead of alongside it. Set by the runtime.
    step: TraceStep | None = None
