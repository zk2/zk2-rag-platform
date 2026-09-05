"""Chat turn orchestration.

Retrieval and generation live in pipeline nodes; this module owns what wraps
them - the conversation, the citation pass, persistence and the usage event.
A bot with its own pipeline and a bot without take the same path: the default
is a DAG like any other.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.ab.service import Assignment, assign, running_experiment
from zk2.auth.models import User
from zk2.bots.models import Bot, BotSource, BotVersion, Conversation, Message
from zk2.chat.events import StreamEvent
from zk2.core.errors import NotFoundError, ValidationError
from zk2.core.quota import KeySource, record_system_tokens
from zk2.core.tracing import start_turn
from zk2.llm.registry import key_source_for
from zk2.pipelines.dag import DagSpec
from zk2.pipelines.defaults import DEFAULT_DAG
from zk2.pipelines.models import Pipeline, PipelineVersion
from zk2.pipelines.runtime import RunReport, execute
from zk2.pipelines.state import NodeContext, PipelineState

logger = structlog.get_logger()

# Markers the model is asked to cite: [1], [2][3], ...
_CITATION_PATTERN = re.compile(r"\[(\d{1,2})\]")


async def _load_bot(db: AsyncSession, *, org_id: int, bot_id: int) -> tuple[Bot, BotVersion]:
    bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
    if bot is None:
        raise NotFoundError("Bot not found")
    if bot.current_version_id is None:
        raise ValidationError("Bot has no active version")
    version = await db.scalar(select(BotVersion).where(BotVersion.id == bot.current_version_id))
    if version is None:
        raise ValidationError("Bot version missing")
    return bot, version


async def _bot_source_ids(db: AsyncSession, *, bot_id: int) -> list[int]:
    rows = await db.execute(select(BotSource.source_id).where(BotSource.bot_id == bot_id))
    return [source_id for (source_id,) in rows.all()]


async def _resolve_conversation(
    db: AsyncSession,
    *,
    bot: Bot,
    user: User | None,
    conversation_id: int | None,
    first_message: str,
) -> Conversation:
    if conversation_id is not None:
        conv = await db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.bot_id == bot.id
            )
        )
        if conv is None:
            raise NotFoundError("Conversation not found")
        return conv

    conv = Conversation(
        bot_id=bot.id,
        user_id=user.id if user else None,
        title=first_message[:64],
    )
    db.add(conv)
    await db.flush()
    return conv


def parse_citations(answer: str, context_chunks: list[dict[str, Any]]) -> list[int]:
    """Markers the answer actually cited, in the order they first appear.

    Anything outside the range of what was in the prompt is ignored - a model
    that invents [7] when six passages were supplied has cited nothing.
    """
    valid = {chunk["marker"] for chunk in context_chunks}
    seen: list[int] = []
    for raw in _CITATION_PATTERN.findall(answer):
        marker = int(raw)
        if marker in valid and marker not in seen:
            seen.append(marker)
    return seen


def _citations_event(cited: list[dict[str, Any]]) -> StreamEvent:
    return StreamEvent(
        "citations",
        {
            "items": [
                {
                    "chunk_id": c["chunk_id"],
                    "source_id": c["source_id"],
                    "name": c["name"],
                    "ordinal": c["ordinal"],
                    "marker": c["marker"],
                }
                for c in cited
            ]
        },
    )


async def _record_usage(
    db: AsyncSession,
    *,
    org_id: int,
    bot_id: int,
    conversation_id: int,
    version: BotVersion,
    tokens_in: int,
    tokens_out: int,
    cost: Decimal | None,
    assignment: Assignment | None = None,
) -> None:
    """Write a usage_event. No ORM model yet - the billing work adds one.

    `cost` is NULL when the model is not in the catalog: an unknown price
    recorded as zero would quietly under-report spend.
    """
    source = await key_source_for(db, org_id=org_id, provider=version.llm_provider)
    await db.execute(
        text(
            "INSERT INTO usage_events "
            "(org_id, bot_id, conversation_id, event_type, provider, model, "
            " tokens_in, tokens_out, cost_usd, key_source, metadata) "
            "VALUES (:org, :bot, :conv, 'llm_call', :prov, :model, "
            "        :tin, :tout, :cost, :key_source, CAST(:meta AS jsonb))"
        ),
        {
            "org": org_id,
            "bot": bot_id,
            "conv": conversation_id,
            "prov": version.llm_provider,
            "model": version.llm_model,
            "tin": tokens_in,
            "tout": tokens_out,
            "cost": cost,
            "key_source": source.value,
            # Experiment tags live in metadata so analytics can group by variant
            "meta": json.dumps(
                {
                    **({} if cost is not None else {"cost_unknown": True}),
                    **(
                        {
                            "experiment_id": str(assignment.experiment_id),
                            "variant_id": str(assignment.variant_id),
                        }
                        if assignment is not None
                        else {}
                    ),
                }
            ),
        },
    )
    if source is KeySource.SYSTEM:
        await record_system_tokens(org_id, tokens_in + tokens_out)


async def _dag_for_version(db: AsyncSession, version_id: int) -> DagSpec | None:
    version = await db.scalar(select(PipelineVersion).where(PipelineVersion.id == version_id))
    return DagSpec.model_validate(version.dag) if version is not None else None


async def _dag_for_bot(db: AsyncSession, bot: Bot) -> DagSpec:
    """The bot's pipeline, or the built-in default when it has none."""
    if bot.pipeline_id is None:
        return DEFAULT_DAG
    pipeline = await db.scalar(select(Pipeline).where(Pipeline.id == bot.pipeline_id))
    if pipeline is None or pipeline.current_version_id is None:
        logger.warning("chat.pipeline_missing", bot_id=bot.id, pipeline_id=bot.pipeline_id)
        return DEFAULT_DAG
    dag = await _dag_for_version(db, pipeline.current_version_id)
    return dag if dag is not None else DEFAULT_DAG


async def _resolve_experiment(
    db: AsyncSession, *, bot: Bot, user: User | None, conversation_id: int | None
) -> Assignment | None:
    """Which variant this turn belongs to, if an experiment is running.

    The subject is the user when there is one, and the conversation otherwise:
    an anonymous visitor should still see one variant for the whole thread.
    """
    experiment = await running_experiment(db, bot_id=bot.id)
    if experiment is None:
        return None
    subject = f"user:{user.id}" if user else f"conv:{conversation_id or 'new'}"
    return await assign(db, experiment=experiment, subject=subject)


async def stream_rag(
    db: AsyncSession,
    *,
    org_id: int,
    bot_id: int,
    user: User | None,
    conversation_id: int | None,
    user_message: str,
) -> AsyncIterator[StreamEvent]:
    """Run one turn, yielding protocol events as they happen."""
    bot, version = await _load_bot(db, org_id=org_id, bot_id=bot_id)
    source_ids = await _bot_source_ids(db, bot_id=bot.id)

    assignment = await _resolve_experiment(db, bot=bot, user=user, conversation_id=conversation_id)
    dag = None
    if assignment is not None and assignment.pipeline_version_id is not None:
        dag = await _dag_for_version(db, assignment.pipeline_version_id)
    if dag is None:
        dag = await _dag_for_bot(db, bot)

    turn = start_turn(
        "rag.turn",
        metadata={
            "org_id": org_id,
            "bot_id": bot.id,
            "provider": version.llm_provider,
            "model": version.llm_model,
            "sources": len(source_ids),
            "pipeline_id": bot.pipeline_id or 0,
            "variant": assignment.variant_name if assignment else "",
        },
    )

    conv = await _resolve_conversation(
        db, bot=bot, user=user, conversation_id=conversation_id, first_message=user_message
    )
    db.add(Message(conversation_id=conv.id, role="user", content=user_message))
    await db.flush()
    yield StreamEvent("conversation", {"id": conv.id})

    state = PipelineState(query=user_message, source_ids=source_ids)
    ctx = NodeContext(db=db, org_id=org_id, bot=bot, version=version, trace=turn)
    report = RunReport()
    started = time.perf_counter()

    async for event in execute(dag, state, ctx, report=report):
        yield event

    if state.failed:
        turn.end(level="ERROR")
        return

    total_ms = int((time.perf_counter() - started) * 1000)

    # Which passages the answer leaned on, as opposed to which were retrieved
    cited_markers = parse_citations(state.answer, state.context_chunks)
    for entry in state.context_chunks:
        entry["cited"] = entry["marker"] in cited_markers
    cited = [c for c in state.context_chunks if c["cited"]]

    turn.end(
        output={"citations": len(cited), "chunks_in_context": len(state.context_chunks)},
        nodes=len(report.timings),
    )
    if cited:
        yield _citations_event(cited)

    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=state.answer,
            sources=state.context_chunks or None,
            tokens_in=state.tokens_in,
            tokens_out=state.tokens_out,
            cost_usd=state.cost_usd,
            latency_ms=state.latency_ms,
        )
    )
    conv.updated_at = datetime.now(UTC)
    await _record_usage(
        db,
        org_id=org_id,
        bot_id=bot.id,
        conversation_id=conv.id,
        version=version,
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
        cost=state.cost_usd,
        assignment=assignment,
    )

    yield StreamEvent(
        "done",
        {
            "tokens_in": state.tokens_in,
            "tokens_out": state.tokens_out,
            "cost_usd": str(state.cost_usd) if state.cost_usd is not None else None,
            "latency_ms": state.latency_ms,
            "pipeline_ms": total_ms,
            "variant": assignment.variant_name if assignment else None,
            "nodes": report.as_dict(),
            # Present only when Langfuse is configured: a link to this exact turn
            "trace_url": turn.trace_url,
        },
    )
