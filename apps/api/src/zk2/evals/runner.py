"""Running a dataset against a bot.

Each item goes through the real pipeline - the same executor a chat turn uses,
so an eval measures the product rather than a reimplementation of it. Scores
are written per item, and their means become the run summary, which is what a
comparison and a regression check read.

Runs cost money: one pipeline turn per item, plus a judge call per judge metric.
The runner reports that cost rather than hiding it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.bots.models import Bot, BotSource, BotVersion
from zk2.core.errors import NotFoundError, ValidationError
from zk2.core.tracing import start_turn
from zk2.evals.metrics import METRICS_BY_NAME, EvalSample
from zk2.evals.models import EvalItem, EvalRun, EvalScore
from zk2.llm.base import LLMProvider
from zk2.llm.registry import get_llm_provider
from zk2.pipelines.dag import DagSpec
from zk2.pipelines.defaults import DEFAULT_DAG
from zk2.pipelines.models import Pipeline, PipelineVersion
from zk2.pipelines.runtime import execute
from zk2.pipelines.state import NodeContext, PipelineState

logger = structlog.get_logger()

DEFAULT_JUDGE_MODEL = "gpt-4.1-mini"


@dataclass(slots=True)
class RunSettings:
    metrics: list[str]
    judge_provider: str = "openai"
    judge_model: str = DEFAULT_JUDGE_MODEL


async def _dag_for_run(db: AsyncSession, run: EvalRun, bot: Bot) -> DagSpec:
    """The graph under test: a pinned version, the bot's pipeline, or the default."""
    if run.pipeline_version_id is not None:
        version = await db.scalar(
            select(PipelineVersion).where(PipelineVersion.id == run.pipeline_version_id)
        )
        if version is None:
            raise NotFoundError("Pipeline version not found")
        return DagSpec.model_validate(version.dag)
    if bot.pipeline_id is not None:
        pipeline = await db.scalar(select(Pipeline).where(Pipeline.id == bot.pipeline_id))
        if pipeline is not None and pipeline.current_version_id is not None:
            version = await db.scalar(
                select(PipelineVersion).where(PipelineVersion.id == pipeline.current_version_id)
            )
            if version is not None:
                return DagSpec.model_validate(version.dag)
    return DEFAULT_DAG


async def _score_item(
    state: PipelineState,
    item: EvalItem,
    settings: RunSettings,
    judge: LLMProvider | None,
) -> dict[str, float]:
    sample = EvalSample(
        question=item.question,
        answer=state.answer,
        expected_answer=item.expected_answer,
        expected_sources=list(item.expected_sources or []),
        retrieved_sources=[chunk["name"] for chunk in state.context_chunks],
        context="\n\n".join(state.context_parts),
    )
    scores: dict[str, float] = {}
    for name in settings.metrics:
        metric = METRICS_BY_NAME.get(name)
        if metric is None:
            continue
        try:
            scores[name] = await metric.score(sample, judge, settings.judge_model)
        except Exception:
            logger.exception("evals.metric_failed", metric=name, item_id=item.id)
            scores[name] = 0.0
    return scores


def _summarise(scores: list[dict[str, float]]) -> dict[str, float]:
    """Mean per metric across the dataset."""
    if not scores:
        return {}
    names = {name for row in scores for name in row}
    return {
        name: round(sum(row.get(name, 0.0) for row in scores) / len(scores), 4) for name in names
    }


async def execute_run(db: AsyncSession, *, run_id: int, settings: RunSettings) -> EvalRun:
    """Run every item in the dataset and record the scores."""
    run = await db.scalar(select(EvalRun).where(EvalRun.id == run_id))
    if run is None:
        raise NotFoundError("Run not found")
    if run.bot_id is None:
        raise ValidationError("Run has no bot to evaluate")

    bot = await db.scalar(select(Bot).where(Bot.id == run.bot_id))
    if bot is None or bot.current_version_id is None:
        raise ValidationError("Bot is not runnable")
    version = await db.scalar(select(BotVersion).where(BotVersion.id == bot.current_version_id))
    if version is None:
        raise ValidationError("Bot version missing")

    dag = await _dag_for_run(db, run, bot)
    source_ids = [
        source_id
        for (source_id,) in (
            await db.execute(select(BotSource.source_id).where(BotSource.bot_id == bot.id))
        ).all()
    ]
    items = (
        (
            await db.execute(
                select(EvalItem).where(EvalItem.dataset_id == run.dataset_id).order_by(EvalItem.id)
            )
        )
        .scalars()
        .all()
    )

    judge: LLMProvider | None = None
    if any(
        METRICS_BY_NAME[name].needs_judge for name in settings.metrics if name in METRICS_BY_NAME
    ):
        judge = await get_llm_provider(db, org_id=run.org_id, provider=settings.judge_provider)

    run.status = "running"
    run.items_total = len(items)
    run.items_done = 0
    await db.flush()

    started = time.perf_counter()
    all_scores: list[dict[str, float]] = []
    total_cost = Decimal(0)

    for item in items:
        state = PipelineState(query=item.question, source_ids=source_ids)
        ctx = NodeContext(
            db=db,
            org_id=run.org_id,
            bot=bot,
            version=version,
            trace=start_turn("eval.item", metadata={"run_id": run.id, "item_id": item.id}),
        )
        error: str | None = None
        try:
            async for _event in execute(dag, state, ctx):
                pass
        except Exception as exc:
            error = str(exc)[:2000]
            logger.exception("evals.item_failed", run_id=run.id, item_id=item.id)

        scores = {} if error else await _score_item(state, item, settings, judge)
        if not error:
            all_scores.append(scores)
        if state.cost_usd is not None:
            total_cost += state.cost_usd

        db.add(
            EvalScore(
                run_id=run.id,
                item_id=item.id,
                answer=state.answer or None,
                error=error,
                metrics=scores,
                retrieved=[
                    {"name": chunk["name"], "ordinal": chunk["ordinal"]}
                    for chunk in state.context_chunks
                ],
                latency_ms=state.latency_ms,
                cost_usd=state.cost_usd,
            )
        )
        run.items_done += 1
        await db.flush()

    run.summary = _summarise(all_scores)
    run.cost_usd = total_cost
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    run.status = "done" if all_scores or not items else "failed"
    if not all_scores and items:
        run.error = "Every item failed"
    await db.flush()
    logger.info(
        "evals.run_finished",
        run_id=run.id,
        items=len(items),
        summary=run.summary,
        status=run.status,
    )
    return run


def compare(baseline: EvalRun, candidate: EvalRun) -> dict[str, dict[str, float]]:
    """Per-metric delta between two runs, candidate minus baseline."""
    names = set(baseline.summary) | set(candidate.summary)
    return {
        name: {
            "baseline": baseline.summary.get(name, 0.0),
            "candidate": candidate.summary.get(name, 0.0),
            "delta": round(candidate.summary.get(name, 0.0) - baseline.summary.get(name, 0.0), 4),
        }
        for name in sorted(names)
    }


def regressions(comparison: dict[str, dict[str, float]], *, threshold: float) -> list[str]:
    """Metrics that dropped by more than the threshold."""
    return [name for name, row in comparison.items() if row["delta"] < -abs(threshold)]
