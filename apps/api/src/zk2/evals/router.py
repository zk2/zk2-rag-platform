"""Evaluation API: golden sets, runs, comparisons."""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.core.arq import get_arq_dep
from zk2.core.deps import get_db_dep
from zk2.core.errors import PayloadTooLargeError
from zk2.evals.metrics import ALL_METRICS
from zk2.evals.models import EvalDataset, EvalItem, EvalRun, EvalScore
from zk2.evals.schemas import (
    ComparisonDto,
    DatasetCreate,
    DatasetDto,
    ImportResultDto,
    ItemCreate,
    ItemDto,
    RunDto,
    RunStart,
    ScoreDto,
)
from zk2.evals.service import (
    compare_runs,
    import_items,
    item_count,
    require_dataset,
    require_run,
    start_run,
)

router = APIRouter(prefix="/evals", tags=["evals"])

MAX_IMPORT_BYTES = 5 * 1024 * 1024


async def _to_dto(db: AsyncSession, dataset: EvalDataset) -> DatasetDto:
    return DatasetDto(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        created_at=dataset.created_at,
        item_count=await item_count(db, dataset.id),
    )


def _run_dto(run: EvalRun) -> RunDto:
    return RunDto(
        id=run.id,
        dataset_id=run.dataset_id,
        bot_id=run.bot_id,
        pipeline_version_id=run.pipeline_version_id,
        label=run.label,
        status=run.status,
        error=run.error,
        items_total=run.items_total,
        items_done=run.items_done,
        summary=run.summary,
        cost_usd=str(run.cost_usd) if run.cost_usd is not None else None,
        duration_ms=run.duration_ms,
        is_baseline=run.is_baseline,
        created_at=run.created_at,
    )


@router.get("/metrics")
async def list_metrics(
    _ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
) -> list[dict[str, object]]:
    """What can be measured, and which metrics cost a model call per item."""
    return [
        {
            "name": metric.name,
            "needs_judge": metric.needs_judge,
            "doc": (metric.__doc__ or "").strip(),
        }
        for metric in ALL_METRICS
    ]


@router.get("/datasets", response_model=list[DatasetDto])
async def list_datasets(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[DatasetDto]:
    rows = (
        (
            await db.execute(
                select(EvalDataset)
                .where(EvalDataset.org_id == ctx.org_id)
                .order_by(EvalDataset.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _to_dto(db, row) for row in rows]


@router.post("/datasets", response_model=DatasetDto, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    payload: DatasetCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> DatasetDto:
    dataset = EvalDataset(org_id=ctx.org_id, name=payload.name, description=payload.description)
    db.add(dataset)
    await db.flush()
    return await _to_dto(db, dataset)


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, str]:
    dataset = await require_dataset(db, org_id=ctx.org_id, dataset_id=dataset_id)
    await db.delete(dataset)
    await db.flush()
    return {"status": "deleted"}


@router.get("/datasets/{dataset_id}/items", response_model=list[ItemDto])
async def list_items(
    dataset_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ItemDto]:
    dataset = await require_dataset(db, org_id=ctx.org_id, dataset_id=dataset_id)
    rows = (
        (
            await db.execute(
                select(EvalItem).where(EvalItem.dataset_id == dataset.id).order_by(EvalItem.id)
            )
        )
        .scalars()
        .all()
    )
    return [ItemDto.model_validate(row) for row in rows]


@router.post("/datasets/{dataset_id}/items", response_model=ItemDto, status_code=201)
async def add_item(
    dataset_id: int,
    payload: ItemCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ItemDto:
    dataset = await require_dataset(db, org_id=ctx.org_id, dataset_id=dataset_id)
    item = EvalItem(
        dataset_id=dataset.id,
        question=payload.question,
        expected_answer=payload.expected_answer,
        expected_sources=payload.expected_sources,
        tags=payload.tags,
    )
    db.add(item)
    await db.flush()
    return ItemDto.model_validate(item)


@router.post("/datasets/{dataset_id}/import", response_model=ImportResultDto)
async def import_dataset(
    dataset_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    file: Annotated[UploadFile, File()],
) -> ImportResultDto:
    """Upload a golden set as CSV or JSON."""
    dataset = await require_dataset(db, org_id=ctx.org_id, dataset_id=dataset_id)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise PayloadTooLargeError(f"Import exceeds {MAX_IMPORT_BYTES} bytes")
    return await import_items(
        db, dataset=dataset, filename=file.filename or "items.json", content=content
    )


@router.post("/datasets/{dataset_id}/runs", response_model=RunDto, status_code=201)
async def start_eval_run(
    dataset_id: int,
    payload: RunStart,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    arq: Annotated[ArqRedis, Depends(get_arq_dep)],
) -> RunDto:
    """Queue a run. One pipeline turn per item, plus a judge call per judge metric."""
    run = await start_run(
        db, arq, org_id=ctx.org_id, user=ctx.user, dataset_id=dataset_id, payload=payload
    )
    return _run_dto(run)


@router.get("/runs", response_model=list[RunDto])
async def list_runs(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    dataset_id: int | None = None,
) -> list[RunDto]:
    query = select(EvalRun).where(EvalRun.org_id == ctx.org_id)
    if dataset_id is not None:
        query = query.where(EvalRun.dataset_id == dataset_id)
    rows = (await db.execute(query.order_by(EvalRun.created_at.desc()).limit(50))).scalars().all()
    return [_run_dto(row) for row in rows]


@router.get("/runs/{run_id}", response_model=RunDto)
async def get_run(
    run_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> RunDto:
    return _run_dto(await require_run(db, org_id=ctx.org_id, run_id=run_id))


@router.get("/runs/{run_id}/scores", response_model=list[ScoreDto])
async def get_scores(
    run_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ScoreDto]:
    run = await require_run(db, org_id=ctx.org_id, run_id=run_id)
    rows = (
        (
            await db.execute(
                select(EvalScore).where(EvalScore.run_id == run.id).order_by(EvalScore.id)
            )
        )
        .scalars()
        .all()
    )
    return [ScoreDto.model_validate(row) for row in rows]


@router.get("/runs/{run_id}/compare/{candidate_run_id}", response_model=ComparisonDto)
async def compare_two_runs(
    run_id: int,
    candidate_run_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    threshold: float = 0.05,
) -> ComparisonDto:
    """Baseline against candidate, with the metrics that regressed named."""
    return await compare_runs(
        db,
        org_id=ctx.org_id,
        baseline_run_id=run_id,
        candidate_run_id=candidate_run_id,
        threshold=threshold,
    )
