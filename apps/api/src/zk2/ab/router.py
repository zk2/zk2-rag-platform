"""A/B experiment API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.ab.models import AbExperiment, AbVariant
from zk2.ab.schemas import (
    ExperimentCreate,
    ExperimentDto,
    PromoteRequest,
    VariantDto,
    VariantStatsDto,
)
from zk2.ab.service import analytics, require_experiment, start, stop, validate_split
from zk2.auth.rbac import OrgContext, require_org
from zk2.bots.models import Bot
from zk2.core.audit import write_audit
from zk2.core.deps import get_db_dep
from zk2.core.errors import NotFoundError, ValidationError
from zk2.pipelines.models import Pipeline, PipelineVersion

router = APIRouter(prefix="/experiments", tags=["ab"])


async def _to_dto(db: AsyncSession, experiment: AbExperiment) -> ExperimentDto:
    variants = (
        (await db.execute(select(AbVariant).where(AbVariant.experiment_id == experiment.id)))
        .scalars()
        .all()
    )
    return ExperimentDto(
        id=experiment.id,
        bot_id=experiment.bot_id,
        name=experiment.name,
        status=experiment.status,
        started_at=experiment.started_at,
        stopped_at=experiment.stopped_at,
        created_at=experiment.created_at,
        variants=[VariantDto.model_validate(v) for v in variants],
    )


@router.get("", response_model=list[ExperimentDto])
async def list_experiments(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    bot_id: int | None = None,
) -> list[ExperimentDto]:
    query = select(AbExperiment).where(AbExperiment.org_id == ctx.org_id)
    if bot_id is not None:
        query = query.where(AbExperiment.bot_id == bot_id)
    rows = (await db.execute(query.order_by(AbExperiment.created_at.desc()))).scalars().all()
    return [await _to_dto(db, row) for row in rows]


@router.post("", response_model=ExperimentDto, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    payload: ExperimentCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ExperimentDto:
    bot = await db.scalar(select(Bot).where(Bot.id == payload.bot_id, Bot.org_id == ctx.org_id))
    if bot is None:
        raise NotFoundError("Bot not found")

    experiment = AbExperiment(
        org_id=ctx.org_id,
        bot_id=bot.id,
        name=payload.name,
        created_by_user_id=ctx.user.id,
    )
    db.add(experiment)
    await db.flush()

    for variant in payload.variants:
        if variant.pipeline_version_id is not None:
            version = await db.scalar(
                select(PipelineVersion).where(PipelineVersion.id == variant.pipeline_version_id)
            )
            if version is None:
                raise NotFoundError(f"Pipeline version {variant.pipeline_version_id} not found")
        db.add(
            AbVariant(
                experiment_id=experiment.id,
                name=variant.name,
                pipeline_version_id=variant.pipeline_version_id,
                traffic_percent=variant.traffic_percent,
                is_control=variant.is_control,
            )
        )
    await db.flush()

    variants = (
        (await db.execute(select(AbVariant).where(AbVariant.experiment_id == experiment.id)))
        .scalars()
        .all()
    )
    # Refuse a split that cannot be run rather than storing a broken draft
    validate_split(list(variants))
    return await _to_dto(db, experiment)


@router.post("/{experiment_id}/start", response_model=ExperimentDto)
async def start_experiment(
    experiment_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ExperimentDto:
    experiment = await require_experiment(db, org_id=ctx.org_id, experiment_id=experiment_id)
    await start(db, experiment)
    await write_audit(
        db,
        action="ab.experiment.started",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=str(experiment.id),
    )
    return await _to_dto(db, experiment)


@router.post("/{experiment_id}/stop", response_model=ExperimentDto)
async def stop_experiment(
    experiment_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ExperimentDto:
    experiment = await require_experiment(db, org_id=ctx.org_id, experiment_id=experiment_id)
    await stop(db, experiment)
    return await _to_dto(db, experiment)


@router.get("/{experiment_id}/stats", response_model=list[VariantStatsDto])
async def experiment_stats(
    experiment_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[VariantStatsDto]:
    """Traffic and spend per variant, from the usage events the turns wrote."""
    experiment = await require_experiment(db, org_id=ctx.org_id, experiment_id=experiment_id)
    rows = await analytics(db, org_id=ctx.org_id, experiment=experiment)
    return [VariantStatsDto(**row) for row in rows]


@router.post("/{experiment_id}/promote", response_model=ExperimentDto)
async def promote_variant(
    experiment_id: int,
    payload: PromoteRequest,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ExperimentDto:
    """Make a variant's pipeline version the bot's live one and stop the test."""
    experiment = await require_experiment(db, org_id=ctx.org_id, experiment_id=experiment_id)
    variant = await db.scalar(
        select(AbVariant).where(
            AbVariant.id == payload.variant_id, AbVariant.experiment_id == experiment.id
        )
    )
    if variant is None:
        raise NotFoundError("Variant not found")
    if variant.pipeline_version_id is None:
        raise ValidationError("The control runs the bot's current pipeline; nothing to promote")

    version = await db.scalar(
        select(PipelineVersion).where(PipelineVersion.id == variant.pipeline_version_id)
    )
    if version is None:
        raise NotFoundError("Pipeline version not found")
    pipeline = await db.scalar(select(Pipeline).where(Pipeline.id == version.pipeline_id))
    if pipeline is None:
        raise NotFoundError("Pipeline not found")

    pipeline.current_version_id = version.id
    bot = await db.scalar(select(Bot).where(Bot.id == experiment.bot_id))
    if bot is not None:
        bot.pipeline_id = pipeline.id
    await stop(db, experiment)
    await write_audit(
        db,
        action="ab.variant.promoted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=str(experiment.id),
        payload={"variant": variant.name, "pipeline_version_id": version.id},
    )
    return await _to_dto(db, experiment)
