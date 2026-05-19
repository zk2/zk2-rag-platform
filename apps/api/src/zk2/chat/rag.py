"""RAG orchestration: retrieve → assemble context → stream from LLM."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.bots.models import Bot, BotSource, BotVersion, Conversation, Message
from zk2.core.errors import NotFound, ValidationFailed
from zk2.llm.base import LLMProvider, Message as LLMMessage
from zk2.llm.openai_provider import OpenAIEmbeddings  # noqa: F401  (default emb model)
from zk2.llm.registry import get_embedding_provider, get_llm_provider
from zk2.retrieval.dense import RetrievedChunk, dense_search
from zk2.sources.chunking import count_tokens

logger = structlog.get_logger()

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
CONTEXT_TOKEN_BUDGET = 6000  # rough; leaves room for system prompt + answer


@dataclass(slots=True)
class StreamEvent:
    kind: str  # token | sources | done | error
    payload: dict[str, Any]


async def stream_rag(
    db: AsyncSession,
    *,
    org_id: int,
    bot_id: int,
    user: User | None,
    conversation_id: int | None,
    user_message: str,
) -> AsyncIterator[StreamEvent]:
    bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
    if bot is None:
        raise NotFound("Bot not found")
    if bot.current_version_id is None:
        raise ValidationFailed("Bot has no active version")
    version = await db.scalar(
        select(BotVersion).where(BotVersion.id == bot.current_version_id)
    )
    if version is None:
        raise ValidationFailed("Bot version missing")

    source_ids = [
        s for (s,) in (
            await db.execute(select(BotSource.source_id).where(BotSource.bot_id == bot.id))
        ).all()
    ]

    # ─── Conversation
    if conversation_id is not None:
        conv = await db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.bot_id == bot.id
            )
        )
        if conv is None:
            raise NotFound("Conversation not found")
    else:
        conv = Conversation(
            bot_id=bot.id,
            user_id=user.id if user else None,
            title=user_message[:64],
        )
        db.add(conv)
        await db.flush()

    db.add(Message(conversation_id=conv.id, role="user", content=user_message))
    await db.flush()
    yield StreamEvent("conversation", {"id": conv.id})

    # ─── Retrieval
    retrieved: list[RetrievedChunk] = []
    if source_ids:
        emb = await get_embedding_provider(
            db, org_id=org_id, model=DEFAULT_EMBEDDING_MODEL
        )
        q_vec = await emb.embed_query(user_message)
        retrieved = await dense_search(
            db,
            org_id=org_id,
            source_ids=source_ids,
            query_embedding=q_vec,
            embedding_model=emb.model,
            k=version.num_k,
        )
        yield StreamEvent(
            "sources",
            {
                "items": [
                    {
                        "chunk_id": r.chunk_id,
                        "source_id": r.source_id,
                        "name": r.source_name,
                        "ordinal": r.ordinal,
                        "score": round(1.0 - r.distance, 4),
                    }
                    for r in retrieved
                ]
            },
        )

    # ─── Context assembly
    context_parts: list[str] = []
    used_tokens = 0
    used_chunks: list[dict[str, Any]] = []
    for r in retrieved:
        snippet = f"[doc:{r.source_name}#{r.ordinal}]\n{r.text}"
        t = count_tokens(snippet)
        if used_tokens + t > CONTEXT_TOKEN_BUDGET:
            break
        used_tokens += t
        context_parts.append(snippet)
        used_chunks.append(
            {
                "chunk_id": r.chunk_id,
                "source_id": r.source_id,
                "name": r.source_name,
                "ordinal": r.ordinal,
            }
        )

    system_prompt = (version.system_prompt or "You are a helpful assistant.").strip()
    if context_parts:
        system_prompt += (
            "\n\nUse only the following retrieved context to answer. "
            "If the context does not contain the answer, say so honestly.\n\n"
            "----- CONTEXT -----\n" + "\n\n".join(context_parts)
        )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]

    # ─── LLM stream
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("rag.llm_failed")
        yield StreamEvent("error", {"message": str(exc)})
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    full_answer = "".join(parts)
    cost = llm.estimate_cost(version.llm_model, tokens_in=tokens_in, tokens_out=tokens_out)

    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=full_answer,
            sources=used_chunks or None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency_ms,
        )
    )
    conv.updated_at = datetime.now(UTC)

    # Usage event for billing/analytics
    from zk2.bots.models import Conversation as _Conv  # noqa: F401

    from zk2.bots.models import Bot as _Bot  # noqa: F401

    from sqlalchemy import insert

    from zk2.bots.models import Message as _Msg  # noqa: F401

    from zk2.bots.models import Bot as _B  # noqa: F401

    # Direct insert into usage_events table (no ORM model defined yet to keep scope small)
    from sqlalchemy import text as _text

    await db.execute(
        _text(
            "INSERT INTO usage_events "
            "(org_id, bot_id, conversation_id, event_type, provider, model, "
            " tokens_in, tokens_out, cost_usd, metadata) "
            "VALUES (:org, :bot, :conv, 'llm_call', :prov, :model, "
            "        :tin, :tout, :cost, :meta::jsonb)"
        ),
        {
            "org": org_id,
            "bot": bot.id,
            "conv": conv.id,
            "prov": version.llm_provider,
            "model": version.llm_model,
            "tin": tokens_in,
            "tout": tokens_out,
            "cost": cost,
            "meta": "{}",
        },
    )

    yield StreamEvent(
        "done",
        {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": str(cost),
            "latency_ms": latency_ms,
        },
    )
