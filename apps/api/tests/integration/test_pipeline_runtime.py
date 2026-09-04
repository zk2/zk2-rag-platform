"""Executing a pipeline: the default one, and custom graphs."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from tests.integration.test_rag_stream import ANSWER_PARTS, FakeLLM
from zk2.auth.models import User
from zk2.bots.schemas import BotCreate
from zk2.bots.service import create_bot
from zk2.core.tracing import start_turn
from zk2.pipelines.dag import DagSpec
from zk2.pipelines.defaults import DEFAULT_DAG
from zk2.pipelines.runtime import RunReport, execute
from zk2.pipelines.state import NodeContext, PipelineState
from zk2.sources.service import create_file_source

pytestmark = pytest.mark.integration


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()

    async def _get_llm(*_args: object, **_kwargs: object) -> FakeLLM:
        return llm

    async def _get_embeddings(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.pipelines.nodes.get_llm_provider", _get_llm)
    monkeypatch.setattr("zk2.pipelines.nodes.get_embedding_provider", _get_embeddings)
    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _get_embeddings)
    return llm


async def _context(db: AsyncSession, org_owner: dict[str, Any]) -> tuple[NodeContext, list[int]]:
    source = await create_file_source(
        db,
        None,
        org_id=org_owner["org_id"],
        parent_id=None,
        filename="alpha.txt",
        content=b"alpha alpha alpha is documented here",
    )
    user = await db.scalar(select(User).where(User.id == org_owner["user_id"]))
    assert user is not None
    bot = await create_bot(
        db,
        org_id=org_owner["org_id"],
        user=user,
        payload=BotCreate(name="Helper", llm_model="gpt-4.1-mini", source_ids=[source.id]),
    )
    await db.commit()

    from zk2.bots.models import Bot, BotVersion

    bot_row = await db.scalar(select(Bot).where(Bot.id == bot.id))
    assert bot_row is not None
    version = await db.scalar(select(BotVersion).where(BotVersion.id == bot_row.current_version_id))
    assert version is not None
    ctx = NodeContext(
        db=db,
        org_id=org_owner["org_id"],
        bot=bot_row,
        version=version,
        trace=start_turn("test.turn"),
    )
    return ctx, [source.id]


async def _run(dag: DagSpec, ctx: NodeContext, source_ids: list[int], query: str = "alpha"):
    state = PipelineState(query=query, source_ids=source_ids)
    report = RunReport()
    events = [event async for event in execute(dag, state, ctx, report=report)]
    return state, events, report


async def test_default_pipeline_produces_context_and_an_answer(
    db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    ctx, source_ids = await _context(db, org_owner)
    state, events, report = await _run(DEFAULT_DAG, ctx, source_ids)

    assert state.answer == "".join(ANSWER_PARTS)
    assert state.context_chunks and state.context_chunks[0]["marker"] == 1
    assert [e.kind for e in events][:1] == ["sources"]
    assert [t.node_id for t in report.timings] == [
        "bm25",
        "dense",
        "fuse",
        "rerank",
        "context",
        "answer",
    ]


async def test_node_timings_are_recorded_for_every_node(
    db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    ctx, source_ids = await _context(db, org_owner)
    _, _, report = await _run(DEFAULT_DAG, ctx, source_ids)
    assert all(t.duration_ms >= 0 for t in report.timings)
    assert {t.node_type for t in report.timings} == {
        "retriever_bm25",
        "retriever_dense",
        "fusion",
        "rerank",
        "context_builder",
        "generate",
    }


async def test_a_lexical_only_pipeline_skips_embeddings(
    db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    """A pipeline is data: dropping dense retrieval needs no code change."""
    ctx, source_ids = await _context(db, org_owner)
    dag = DagSpec.model_validate(
        {
            "nodes": [
                {"id": "bm25", "type": "retriever_bm25", "config": {"k": 10}},
                {"id": "fuse", "type": "fusion", "config": {"limit": 5}},
                {"id": "context", "type": "context_builder", "config": {"token_budget": 2000}},
                {"id": "answer", "type": "generate", "config": {}},
            ],
            "edges": [["bm25", "fuse"], ["fuse", "context"], ["context", "answer"]],
        }
    )
    state, _, report = await _run(dag, ctx, source_ids, query="documented")
    assert [t.node_id for t in report.timings] == ["bm25", "fuse", "context", "answer"]
    assert state.answer


async def test_generate_config_overrides_the_bot_settings(
    db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    ctx, source_ids = await _context(db, org_owner)
    dag = DagSpec.model_validate(
        {
            "nodes": [
                {
                    "id": "answer",
                    "type": "generate",
                    "config": {"system_prompt": "You are a pirate.", "cite_sources": False},
                }
            ],
            "edges": [],
        }
    )
    await _run(dag, ctx, source_ids)
    system_prompt = fakes.seen_messages[0].content
    assert system_prompt.startswith("You are a pirate.")


async def test_context_budget_limits_the_passages(
    db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    ctx, source_ids = await _context(db, org_owner)
    dag = DagSpec.model_validate(
        {
            "nodes": [
                {"id": "bm25", "type": "retriever_bm25", "config": {"k": 10}},
                {"id": "fuse", "type": "fusion", "config": {"limit": 10}},
                {"id": "context", "type": "context_builder", "config": {"token_budget": 200}},
                {"id": "answer", "type": "generate", "config": {}},
            ],
            "edges": [["bm25", "fuse"], ["fuse", "context"], ["context", "answer"]],
        }
    )
    state, _, _ = await _run(dag, ctx, source_ids, query="documented")
    assert len(state.context_parts) <= 1


async def test_a_failing_generate_stops_the_pipeline(
    db: AsyncSession, org_owner: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fakes: FakeLLM
) -> None:
    ctx, source_ids = await _context(db, org_owner)

    class BrokenLLM(FakeLLM):
        def complete(self, messages: Any, **_kwargs: Any) -> Any:
            async def _stream() -> Any:
                raise RuntimeError("provider is down")
                yield  # pragma: no cover

            return _stream()

    async def _broken(*_args: object, **_kwargs: object) -> BrokenLLM:
        return BrokenLLM()

    monkeypatch.setattr("zk2.pipelines.nodes.get_llm_provider", _broken)
    state, events, _ = await _run(DEFAULT_DAG, ctx, source_ids)
    assert state.failed is True
    assert events[-1].kind == "error"
