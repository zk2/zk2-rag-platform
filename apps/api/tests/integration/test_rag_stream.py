"""One RAG turn end-to-end: retrieval, context, streaming, persistence, usage."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from zk2.auth.models import User
from zk2.bots.models import Conversation, Message
from zk2.bots.schemas import BotCreate
from zk2.bots.service import create_bot
from zk2.chat.rag import stream_rag
from zk2.core.errors import NotFoundError
from zk2.llm.base import CompletionChunk
from zk2.llm.base import Message as LLMMessage
from zk2.sources.service import create_file_source

pytestmark = pytest.mark.integration

ANSWER_PARTS = ["Alpha ", "is ", "documented."]


class FakeLLM:
    """Records the prompt it was given and streams a fixed answer."""

    name = "fake"

    def __init__(self) -> None:
        self.seen_messages: list[LLMMessage] = []

    def complete(
        self, messages: Sequence[LLMMessage], **_kwargs: Any
    ) -> AsyncIterator[CompletionChunk]:
        self.seen_messages = list(messages)

        async def _stream() -> AsyncIterator[CompletionChunk]:
            for part in ANSWER_PARTS:
                yield CompletionChunk(delta=part)
            yield CompletionChunk(tokens_in=120, tokens_out=8, finish_reason="stop")

        return _stream()

    def estimate_cost(self, _model: str, *, tokens_in: int, tokens_out: int) -> Decimal:
        return Decimal(tokens_in + tokens_out) / Decimal(1_000_000)


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()

    async def _get_llm(*_args: object, **_kwargs: object) -> FakeLLM:
        return llm

    async def _get_embeddings(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.chat.rag.get_llm_provider", _get_llm)
    monkeypatch.setattr("zk2.chat.rag.get_embedding_provider", _get_embeddings)
    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _get_embeddings)
    return llm


async def _bot_with_source(db: AsyncSession, *, org_id: int, user_id: int) -> int:
    source = await create_file_source(
        db,
        None,
        org_id=org_id,
        parent_id=None,
        filename="alpha.txt",
        content=b"alpha alpha alpha is documented here",
    )
    user = await db.scalar(select(User).where(User.id == user_id))
    assert user is not None
    bot = await create_bot(
        db,
        org_id=org_id,
        user=user,
        payload=BotCreate(
            name="Helper",
            system_prompt="You are terse.",
            llm_provider="openai",
            llm_model="gpt-4.1-mini",
            source_ids=[source.id],
        ),
    )
    await db.commit()
    return bot.id


async def _collect(events: Any) -> dict[str, list[dict[str, Any]]]:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    async for ev in events:
        by_kind.setdefault(ev.kind, []).append(ev.payload)
    return by_kind


async def test_turn_streams_tokens_sources_and_usage(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM
) -> None:
    org_id = org_owner["org_id"]
    bot_id = await _bot_with_source(db, org_id=org_id, user_id=org_owner["user_id"])
    user = await db.scalar(select(User).where(User.id == org_owner["user_id"]))

    events = await _collect(
        stream_rag(
            db,
            org_id=org_id,
            bot_id=bot_id,
            user=user,
            conversation_id=None,
            user_message="What about alpha?",
        )
    )
    await db.commit()

    assert list(events) == ["conversation", "sources", "token", "done"]
    assert "".join(e["delta"] for e in events["token"]) == "".join(ANSWER_PARTS)

    (done,) = events["done"]
    assert done["tokens_in"] == 120
    assert done["tokens_out"] == 8
    assert Decimal(done["cost_usd"]) > 0
    assert done["latency_ms"] >= 0

    (sources,) = events["sources"]
    assert sources["items"][0]["name"] == "alpha.txt"


async def test_retrieved_context_reaches_the_prompt(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM
) -> None:
    bot_id = await _bot_with_source(db, org_id=org_owner["org_id"], user_id=org_owner["user_id"])
    await _collect(
        stream_rag(
            db,
            org_id=org_owner["org_id"],
            bot_id=bot_id,
            user=None,
            conversation_id=None,
            user_message="alpha",
        )
    )
    system_prompt = fake_llm.seen_messages[0].content
    assert "You are terse." in system_prompt
    assert "----- CONTEXT -----" in system_prompt
    assert "alpha alpha alpha" in system_prompt
    assert fake_llm.seen_messages[1].content == "alpha"


async def test_turn_persists_conversation_messages_and_usage_event(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM
) -> None:
    org_id = org_owner["org_id"]
    bot_id = await _bot_with_source(db, org_id=org_id, user_id=org_owner["user_id"])

    events = await _collect(
        stream_rag(
            db,
            org_id=org_id,
            bot_id=bot_id,
            user=None,
            conversation_id=None,
            user_message="alpha?",
        )
    )
    await db.commit()
    conversation_id = events["conversation"][0]["id"]

    messages = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == "".join(ANSWER_PARTS)
    assert messages[1].sources, "assistant message should carry source attribution"

    usage = (
        await db.execute(text("SELECT event_type, tokens_in, tokens_out FROM usage_events"))
    ).all()
    assert len(usage) == 1
    assert usage[0].event_type == "llm_call"
    assert usage[0].tokens_in == 120


async def test_second_turn_reuses_the_conversation(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM
) -> None:
    org_id = org_owner["org_id"]
    bot_id = await _bot_with_source(db, org_id=org_id, user_id=org_owner["user_id"])

    first = await _collect(
        stream_rag(
            db, org_id=org_id, bot_id=bot_id, user=None, conversation_id=None, user_message="one"
        )
    )
    conversation_id = first["conversation"][0]["id"]
    second = await _collect(
        stream_rag(
            db,
            org_id=org_id,
            bot_id=bot_id,
            user=None,
            conversation_id=conversation_id,
            user_message="two",
        )
    )
    await db.commit()

    assert second["conversation"][0]["id"] == conversation_id
    conversations = (await db.execute(select(Conversation))).scalars().all()
    assert len(conversations) == 1


async def test_unknown_bot_is_rejected(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM
) -> None:
    with pytest.raises(NotFoundError):
        await _collect(
            stream_rag(
                db,
                org_id=org_owner["org_id"],
                bot_id=999_999,
                user=None,
                conversation_id=None,
                user_message="hi",
            )
        )


async def test_llm_failure_surfaces_as_error_event(
    db: AsyncSession, org_owner: dict[str, Any], fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = org_owner["org_id"]
    bot_id = await _bot_with_source(db, org_id=org_id, user_id=org_owner["user_id"])

    class BrokenLLM(FakeLLM):
        def complete(
            self, messages: Sequence[LLMMessage], **_kwargs: Any
        ) -> AsyncIterator[CompletionChunk]:
            async def _stream() -> AsyncIterator[CompletionChunk]:
                raise RuntimeError("provider is down")
                yield  # pragma: no cover

            return _stream()

    async def _broken(*_args: object, **_kwargs: object) -> BrokenLLM:
        return BrokenLLM()

    monkeypatch.setattr("zk2.chat.rag.get_llm_provider", _broken)

    events = await _collect(
        stream_rag(
            db, org_id=org_id, bot_id=bot_id, user=None, conversation_id=None, user_message="alpha"
        )
    )
    assert "error" in events
    assert "provider is down" in events["error"][0]["message"]
    assert "done" not in events
