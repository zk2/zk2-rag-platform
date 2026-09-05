"""Dataset management, run bookkeeping and comparisons."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import structlog
from arq import ArqRedis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.core.errors import NotFoundError, ValidationError
from zk2.evals.metrics import METRICS_BY_NAME
from zk2.evals.models import EvalDataset, EvalItem, EvalRun
from zk2.evals.runner import RunSettings, compare, execute_run, regressions
from zk2.evals.schemas import ComparisonDto, ImportResultDto, RunStart

logger = structlog.get_logger()

MAX_IMPORT_ITEMS = 1000
DEFAULT_REGRESSION_THRESHOLD = 0.05


async def require_dataset(db: AsyncSession, *, org_id: int, dataset_id: int) -> EvalDataset:
    dataset = await db.scalar(
        select(EvalDataset).where(EvalDataset.id == dataset_id, EvalDataset.org_id == org_id)
    )
    if dataset is None:
        raise NotFoundError("Dataset not found")
    return dataset


async def require_run(db: AsyncSession, *, org_id: int, run_id: int) -> EvalRun:
    run = await db.scalar(select(EvalRun).where(EvalRun.id == run_id, EvalRun.org_id == org_id))
    if run is None:
        raise NotFoundError("Run not found")
    return run


async def item_count(db: AsyncSession, dataset_id: int) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(EvalItem).where(EvalItem.dataset_id == dataset_id)
        )
    ).scalar_one()


def _rows_from_csv(raw: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(raw))
    return [dict(row) for row in reader]


def _rows_from_json(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        raise ValidationError('JSON must be a list of items or {"items": [...]}')
    return [row for row in payload if isinstance(row, dict)]


def _split_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


async def import_items(
    db: AsyncSession, *, dataset: EvalDataset, filename: str, content: bytes
) -> ImportResultDto:
    """Load a golden set from CSV or JSON.

    A malformed row is skipped and reported rather than failing the upload:
    partial import of a hand-edited spreadsheet is the useful behaviour.
    """
    raw = content.decode("utf-8", errors="replace")
    rows = _rows_from_csv(raw) if filename.lower().endswith(".csv") else _rows_from_json(raw)
    if len(rows) > MAX_IMPORT_ITEMS:
        raise ValidationError(f"At most {MAX_IMPORT_ITEMS} items per import")

    imported = 0
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        question = str(row.get("question") or "").strip()
        if not question:
            errors.append(f"Row {index}: no question")
            continue
        db.add(
            EvalItem(
                dataset_id=dataset.id,
                question=question,
                expected_answer=(
                    str(row["expected_answer"]) if row.get("expected_answer") else None
                ),
                expected_sources=_split_list(row.get("expected_sources")),
                tags=_split_list(row.get("tags")),
            )
        )
        imported += 1
    await db.flush()
    return ImportResultDto(imported=imported, skipped=len(rows) - imported, errors=errors[:20])


async def start_run(
    db: AsyncSession,
    arq: ArqRedis | None,
    *,
    org_id: int,
    user: User,
    dataset_id: int,
    payload: RunStart,
) -> EvalRun:
    """Create the run row and hand it to the worker (or run it inline in tests)."""
    dataset = await require_dataset(db, org_id=org_id, dataset_id=dataset_id)
    unknown = [name for name in payload.metrics if name not in METRICS_BY_NAME]
    if unknown:
        raise ValidationError(f"Unknown metrics: {', '.join(unknown)}")
    if not payload.metrics:
        raise ValidationError("Pick at least one metric")
    if await item_count(db, dataset.id) == 0:
        raise ValidationError("Dataset has no items")

    run = EvalRun(
        org_id=org_id,
        dataset_id=dataset.id,
        bot_id=payload.bot_id,
        pipeline_version_id=payload.pipeline_version_id,
        label=payload.label,
        status="pending",
        is_baseline=payload.mark_baseline,
        created_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()

    if arq is not None:
        await arq.enqueue_job("run_eval", run.id, payload.model_dump())
    else:
        await execute_run(
            db,
            run_id=run.id,
            settings=RunSettings(
                metrics=payload.metrics,
                judge_provider=payload.judge_provider,
                judge_model=payload.judge_model,
            ),
        )
    return run


async def compare_runs(
    db: AsyncSession,
    *,
    org_id: int,
    baseline_run_id: int,
    candidate_run_id: int,
    threshold: float = DEFAULT_REGRESSION_THRESHOLD,
) -> ComparisonDto:
    baseline = await require_run(db, org_id=org_id, run_id=baseline_run_id)
    candidate = await require_run(db, org_id=org_id, run_id=candidate_run_id)
    if baseline.dataset_id != candidate.dataset_id:
        raise ValidationError("Runs must be over the same dataset to be comparable")

    metrics = compare(baseline, candidate)
    return ComparisonDto(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metrics=metrics,
        regressions=regressions(metrics, threshold=threshold),
        threshold=threshold,
    )


async def latest_baseline(db: AsyncSession, *, org_id: int, dataset_id: int) -> EvalRun | None:
    run: EvalRun | None = await db.scalar(
        select(EvalRun)
        .where(
            EvalRun.org_id == org_id,
            EvalRun.dataset_id == dataset_id,
            EvalRun.is_baseline.is_(True),
            EvalRun.status == "done",
        )
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    )
    return run
