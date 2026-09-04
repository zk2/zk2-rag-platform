"""Pipeline CRUD, versioning and test runs.

Saving never edits a version in place: like bots, every save writes a new
`pipeline_versions` row and moves the pointer, so a rollback is a pointer move
and history is a side effect of normal use rather than a feature to maintain.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.bots.models import Bot, BotSource, BotVersion
from zk2.core.errors import NotFoundError, ValidationError
from zk2.core.tracing import start_turn
from zk2.pipelines.dag import DagSpec
from zk2.pipelines.defaults import DEFAULT_DAG
from zk2.pipelines.models import Pipeline, PipelineRun, PipelineVersion
from zk2.pipelines.runtime import RunReport, execute, validate_dag
from zk2.pipelines.schemas import NodeTimingDto, PipelineDetailDto, TestRunDto
from zk2.pipelines.state import NodeContext, PipelineState

logger = structlog.get_logger()


async def _require_pipeline(db: AsyncSession, *, org_id: int, pipeline_id: int) -> Pipeline:
    pipeline = await db.scalar(
        select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.org_id == org_id)
    )
    if pipeline is None:
        raise NotFoundError("Pipeline not found")
    return pipeline


async def current_dag(db: AsyncSession, pipeline: Pipeline) -> DagSpec:
    if pipeline.current_version_id is None:
        return DEFAULT_DAG
    version = await db.scalar(
        select(PipelineVersion).where(PipelineVersion.id == pipeline.current_version_id)
    )
    return DagSpec.model_validate(version.dag) if version else DEFAULT_DAG


async def create_pipeline(
    db: AsyncSession,
    *,
    org_id: int,
    user: User,
    name: str,
    description: str | None,
    dag: DagSpec | None,
) -> Pipeline:
    """New pipeline, seeded with the built-in default unless a graph is given."""
    graph = dag or DEFAULT_DAG
    validate_dag(graph)

    pipeline = Pipeline(org_id=org_id, name=name, description=description)
    db.add(pipeline)
    await db.flush()

    version = PipelineVersion(
        pipeline_id=pipeline.id,
        dag=graph.model_dump(mode="json"),
        label="initial",
        created_by_user_id=user.id,
    )
    db.add(version)
    await db.flush()
    pipeline.current_version_id = version.id
    await db.flush()
    return pipeline


async def save_version(
    db: AsyncSession,
    *,
    org_id: int,
    pipeline_id: int,
    user: User,
    dag: DagSpec,
    label: str | None,
) -> PipelineVersion:
    """Validate a graph and store it as the new current version."""
    pipeline = await _require_pipeline(db, org_id=org_id, pipeline_id=pipeline_id)
    validate_dag(dag)

    version = PipelineVersion(
        pipeline_id=pipeline.id,
        dag=dag.model_dump(mode="json"),
        label=label,
        created_by_user_id=user.id,
    )
    db.add(version)
    await db.flush()
    pipeline.current_version_id = version.id
    await db.flush()
    logger.info("pipeline.version_saved", pipeline_id=pipeline.id, version_id=version.id)
    return version


async def activate_version(
    db: AsyncSession, *, org_id: int, pipeline_id: int, version_id: int
) -> PipelineVersion:
    """Roll back (or forward) to an existing version - a pointer move."""
    pipeline = await _require_pipeline(db, org_id=org_id, pipeline_id=pipeline_id)
    version = await db.scalar(
        select(PipelineVersion).where(
            PipelineVersion.id == version_id, PipelineVersion.pipeline_id == pipeline.id
        )
    )
    if version is None:
        raise NotFoundError("Version not found")
    pipeline.current_version_id = version.id
    await db.flush()
    return version


async def describe(db: AsyncSession, pipeline: Pipeline) -> PipelineDetailDto:
    dag = await current_dag(db, pipeline)
    version_count = (
        await db.execute(
            select(func.count())
            .select_from(PipelineVersion)
            .where(PipelineVersion.pipeline_id == pipeline.id)
        )
    ).scalar_one()
    bots = (await db.execute(select(Bot.id).where(Bot.pipeline_id == pipeline.id))).scalars().all()
    return PipelineDetailDto(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        current_version_id=pipeline.current_version_id,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        dag=dag,
        version_count=version_count,
        used_by_bots=list(bots),
    )


async def delete_pipeline(db: AsyncSession, *, org_id: int, pipeline_id: int) -> None:
    pipeline = await _require_pipeline(db, org_id=org_id, pipeline_id=pipeline_id)
    in_use = (
        await db.execute(
            select(func.count()).select_from(Bot).where(Bot.pipeline_id == pipeline.id)
        )
    ).scalar_one()
    if in_use:
        raise ValidationError(
            f"{in_use} bot(s) still use this pipeline; point them elsewhere first"
        )
    await db.delete(pipeline)
    await db.flush()


async def test_run(
    db: AsyncSession,
    *,
    org_id: int,
    pipeline_id: int,
    bot_id: int,
    question: str,
    dag: DagSpec | None,
) -> TestRunDto:
    """Run a graph against a bot's sources without touching any conversation.

    The unsaved working copy can be passed in, so the editor can try a change
    before committing it to a version.
    """
    pipeline = await _require_pipeline(db, org_id=org_id, pipeline_id=pipeline_id)
    if pipeline.current_version_id is None:
        raise ValidationError("Pipeline has no saved version")
    graph = dag or await current_dag(db, pipeline)
    validate_dag(graph)

    bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
    if bot is None:
        raise NotFoundError("Bot not found")
    if bot.current_version_id is None:
        raise ValidationError("Bot has no active version")
    version = await db.scalar(select(BotVersion).where(BotVersion.id == bot.current_version_id))
    if version is None:
        raise ValidationError("Bot version missing")

    source_ids = [
        source_id
        for (source_id,) in (
            await db.execute(select(BotSource.source_id).where(BotSource.bot_id == bot.id))
        ).all()
    ]

    turn = start_turn("pipeline.test_run", metadata={"org_id": org_id, "pipeline_id": pipeline.id})
    state = PipelineState(query=question, source_ids=source_ids)
    ctx = NodeContext(db=db, org_id=org_id, bot=bot, version=version, trace=turn)
    report = RunReport()

    sources: list[dict[str, Any]] = []
    error: str | None = None
    started = time.perf_counter()
    async for event in execute(graph, state, ctx, report=report):
        if event.kind == "sources":
            sources = event.payload["items"]
        elif event.kind == "error":
            error = str(event.payload.get("message"))
    duration_ms = int((time.perf_counter() - started) * 1000)
    turn.end(output={"status": "failed" if state.failed else "ok"})

    from zk2.chat.rag import parse_citations  # noqa: PLC0415  (cycle: chat -> pipelines)

    citations = parse_citations(state.answer, state.context_chunks)

    db.add(
        PipelineRun(
            org_id=org_id,
            pipeline_version_id=pipeline.current_version_id,
            bot_id=bot.id,
            status="failed" if state.failed else "ok",
            question=question,
            answer=state.answer or None,
            error=error,
            duration_ms=duration_ms,
            node_timings=report.as_dict(),
        )
    )
    await db.flush()

    return TestRunDto(
        status="failed" if state.failed else "ok",
        question=question,
        answer=state.answer or None,
        error=error,
        duration_ms=duration_ms,
        nodes=[NodeTimingDto(**timing) for timing in report.as_dict()],
        sources=sources,
        citations=citations,
        tokens_in=state.tokens_in,
        tokens_out=state.tokens_out,
        cost_usd=str(state.cost_usd) if state.cost_usd is not None else None,
        trace_url=turn.trace_url,
    )
