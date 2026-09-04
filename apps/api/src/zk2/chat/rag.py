"""RAG orchestration: retrieve → assemble context → stream from LLM."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.bots.models import Bot, BotSource, BotVersion, Conversation, Message
from zk2.config import get_settings
from zk2.core.errors import NotFoundError, ValidationError
from zk2.llm.base import LLMProvider
from zk2.llm.base import Message as LLMMessage
from zk2.llm.registry import get_embedding_provider, get_llm_provider
from zk2.retrieval.base import RetrievedChunk
from zk2.retrieval.bm25 import bm25_search
from zk2.retrieval.dense import dense_search
from zk2.retrieval.fusion import reciprocal_rank_fusion
from zk2.retrieval.rerank import rerank
from zk2.sources.chunking import count_tokens

logger = structlog.get_logger()

CONTEXT_TOKEN_BUDGET = 6000  # rough; leaves room for system prompt + answer
# Each retriever returns more than the final k so fusion has something to fuse
CANDIDATE_MULTIPLIER = 4
MIN_CANDIDATES = 20
# Markers the model is asked to cite: [1], [2][3], ...
_CITATION_PATTERN = re.compile(r"\[(\d{1,2})\]")


@dataclass(slots=True)
class StreamEvent:
    kind: str  # token | sources | done | error
    payload: dict[str, Any]


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


async def _retrieve(
    db: AsyncSession,
    *,
    org_id: int,
    source_ids: list[int],
    query: str,
    k: int,
) -> list[RetrievedChunk]:
    """Hybrid retrieval: dense and lexical candidates fused by RRF.

    The two run in sequence rather than concurrently: an AsyncSession is a
    single connection and will not serve two queries at once.
    """
    if not source_ids:
        return []
    candidates = max(k * CANDIDATE_MULTIPLIER, MIN_CANDIDATES)

    emb = await get_embedding_provider(db, org_id=org_id)
    query_embedding = await emb.embed_query(query)
    dense_hits = await dense_search(
        db,
        org_id=org_id,
        source_ids=source_ids,
        query_embedding=query_embedding,
        embedding_model=emb.model,
        k=candidates,
    )
    lexical_hits = await bm25_search(
        db, org_id=org_id, source_ids=source_ids, query=query, k=candidates
    )
    fused = reciprocal_rank_fusion(
        [dense_hits, lexical_hits], limit=get_settings().retrieval.rerank_candidates
    )
    logger.debug(
        "rag.retrieved",
        dense=len(dense_hits),
        bm25=len(lexical_hits),
        fused=len(fused),
        candidates=candidates,
    )
    # Reranking reads query and passage together, so it only runs on the short
    # fused list. Disabled or unavailable, the fused order stands.
    return await rerank(query, fused, top_k=k)


def _assemble_context(retrieved: list[RetrievedChunk]) -> tuple[list[str], list[dict[str, Any]]]:
    """Pack retrieved chunks into the token budget, numbered for citation.

    The number is what makes attribution possible: the model is asked to cite
    [1], [2] and so on, and those markers are matched back to chunks after the
    answer is generated.
    """
    context_parts: list[str] = []
    used_chunks: list[dict[str, Any]] = []
    used_tokens = 0
    for r in retrieved:
        marker = len(used_chunks) + 1
        snippet = f"[{marker}] source: {r.source_name} (chunk {r.ordinal})\n{r.text}"
        tokens = count_tokens(snippet)
        if used_tokens + tokens > CONTEXT_TOKEN_BUDGET:
            break
        used_tokens += tokens
        context_parts.append(snippet)
        used_chunks.append(
            {
                "marker": marker,
                "chunk_id": r.chunk_id,
                "source_id": r.source_id,
                "name": r.source_name,
                "ordinal": r.ordinal,
                "cited": False,
            }
        )
    return context_parts, used_chunks


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


def _build_messages(
    *, version: BotVersion, context_parts: list[str], user_message: str
) -> list[LLMMessage]:
    system_prompt = (version.system_prompt or "You are a helpful assistant.").strip()
    if context_parts:
        system_prompt += (
            "\n\nAnswer using only the numbered passages below. "
            "If they do not contain the answer, say so honestly.\n"
            "Cite the passages you actually used by their number in square "
            "brackets, like [1] or [2][3], right after the statement they "
            "support. Do not cite a passage you did not use.\n\n"
            "----- CONTEXT -----\n" + "\n\n".join(context_parts)
        )
    return [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]


def _sources_event(retrieved: list[RetrievedChunk]) -> StreamEvent:
    return StreamEvent(
        "sources",
        {
            "items": [
                {
                    "chunk_id": r.chunk_id,
                    "source_id": r.source_id,
                    "name": r.source_name,
                    "ordinal": r.ordinal,
                    "score": round(r.score, 6),
                    "matched_by": list(r.matched_by) or [r.retriever],
                }
                for r in retrieved
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
) -> None:
    """Write a usage_event. No ORM model yet - the billing work adds one.

    `cost` is NULL when the model is not in the catalog: an unknown price
    recorded as zero would quietly under-report spend.
    """
    await db.execute(
        text(
            "INSERT INTO usage_events "
            "(org_id, bot_id, conversation_id, event_type, provider, model, "
            " tokens_in, tokens_out, cost_usd, metadata) "
            "VALUES (:org, :bot, :conv, 'llm_call', :prov, :model, "
            "        :tin, :tout, :cost, CAST(:meta AS jsonb))"
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
            "meta": "{}" if cost is not None else '{"cost_unknown": true}',
        },
    )


async def stream_rag(
    db: AsyncSession,
    *,
    org_id: int,
    bot_id: int,
    user: User | None,
    conversation_id: int | None,
    user_message: str,
) -> AsyncIterator[StreamEvent]:
    """Run one RAG turn, yielding protocol events as they happen."""
    bot, version = await _load_bot(db, org_id=org_id, bot_id=bot_id)
    source_ids = await _bot_source_ids(db, bot_id=bot.id)

    conv = await _resolve_conversation(
        db, bot=bot, user=user, conversation_id=conversation_id, first_message=user_message
    )
    db.add(Message(conversation_id=conv.id, role="user", content=user_message))
    await db.flush()
    yield StreamEvent("conversation", {"id": conv.id})

    retrieved = await _retrieve(
        db, org_id=org_id, source_ids=source_ids, query=user_message, k=version.num_k
    )
    if retrieved:
        yield _sources_event(retrieved)

    context_parts, used_chunks = _assemble_context(retrieved)
    messages = _build_messages(
        version=version, context_parts=context_parts, user_message=user_message
    )

    llm: LLMProvider = await get_llm_provider(db, org_id=org_id, provider=version.llm_provider)
    started = time.perf_counter()
    parts: list[str] = []
    tokens_in = tokens_out = 0

    try:
        async for chunk in llm.complete(
            messages,
            model=version.llm_model,
            temperature=version.temperature,
            stream=True,
        ):
            if chunk.delta:
                parts.append(chunk.delta)
                yield StreamEvent("token", {"delta": chunk.delta})
            if chunk.tokens_in is not None:
                tokens_in = chunk.tokens_in
            if chunk.tokens_out is not None:
                tokens_out = chunk.tokens_out
    except Exception as exc:
        logger.exception("rag.llm_failed")
        yield StreamEvent("error", {"message": str(exc)})
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    answer = "".join(parts)
    cost = llm.estimate_cost(version.llm_model, tokens_in=tokens_in, tokens_out=tokens_out)

    # Which passages the answer leaned on, as opposed to which were retrieved
    cited_markers = parse_citations(answer, used_chunks)
    for entry in used_chunks:
        entry["cited"] = entry["marker"] in cited_markers
    cited = [c for c in used_chunks if c["cited"]]
    if cited:
        yield _citations_event(cited)

    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            sources=used_chunks or None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency_ms,
        )
    )
    conv.updated_at = datetime.now(UTC)
    await _record_usage(
        db,
        org_id=org_id,
        bot_id=bot.id,
        conversation_id=conv.id,
        version=version,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )

    yield StreamEvent(
        "done",
        {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": str(cost) if cost is not None else None,
            "latency_ms": latency_ms,
        },
    )
