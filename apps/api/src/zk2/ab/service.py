"""Assignment and analytics for A/B experiments.

Assignment is a hash, not a coin flip: the same subject lands in the same
variant on every turn, across restarts, without storing anything. The
assignment is then persisted anyway - so that changing the traffic split later
does not silently move people who have already seen a variant, which would make
the comparison meaningless.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.ab.models import AbAssignment, AbExperiment, AbVariant
from zk2.core.errors import NotFoundError, ValidationError

logger = structlog.get_logger()

BUCKETS = 100


@dataclass(frozen=True, slots=True)
class Assignment:
    experiment_id: int
    variant_id: int
    variant_name: str
    pipeline_version_id: int | None


def bucket_of(experiment_id: int, subject: str) -> int:
    """Stable bucket in [0, 100) for a subject within one experiment."""
    digest = hashlib.sha256(f"{experiment_id}:{subject}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def pick_variant(variants: list[AbVariant], bucket: int) -> AbVariant:
    """Walk the split in a fixed order so a bucket always maps to one variant."""
    ordered = sorted(variants, key=lambda v: v.id)
    edge = 0
    for variant in ordered:
        edge += variant.traffic_percent
        if bucket < edge:
            return variant
    return ordered[-1]


def validate_split(variants: list[AbVariant]) -> None:
    total = sum(v.traffic_percent for v in variants)
    if total != 100:
        raise ValidationError(f"Traffic split must add up to 100, got {total}")
    if len(variants) < 2:
        raise ValidationError("An experiment needs at least two variants")
    if sum(1 for v in variants if v.is_control) != 1:
        raise ValidationError("Exactly one variant must be the control")


async def running_experiment(db: AsyncSession, *, bot_id: int) -> AbExperiment | None:
    experiment: AbExperiment | None = await db.scalar(
        select(AbExperiment)
        .where(AbExperiment.bot_id == bot_id, AbExperiment.status == "running")
        .order_by(AbExperiment.started_at.desc())
        .limit(1)
    )
    return experiment


async def assign(db: AsyncSession, *, experiment: AbExperiment, subject: str) -> Assignment | None:
    """Which variant this subject sees - the same one every time."""
    variants = (
        (await db.execute(select(AbVariant).where(AbVariant.experiment_id == experiment.id)))
        .scalars()
        .all()
    )
    if not variants:
        return None

    existing = await db.scalar(
        select(AbAssignment).where(
            AbAssignment.experiment_id == experiment.id, AbAssignment.subject == subject
        )
    )
    if existing is not None:
        variant = next((v for v in variants if v.id == existing.variant_id), None)
        if variant is not None:
            return Assignment(experiment.id, variant.id, variant.name, variant.pipeline_version_id)

    variant = pick_variant(list(variants), bucket_of(experiment.id, subject))
    db.add(AbAssignment(experiment_id=experiment.id, subject=subject, variant_id=variant.id))
    await db.flush()
    logger.info("ab.assigned", experiment_id=experiment.id, subject=subject, variant=variant.name)
    return Assignment(experiment.id, variant.id, variant.name, variant.pipeline_version_id)


async def start(db: AsyncSession, experiment: AbExperiment) -> AbExperiment:
    variants = (
        (await db.execute(select(AbVariant).where(AbVariant.experiment_id == experiment.id)))
        .scalars()
        .all()
    )
    validate_split(list(variants))

    other = await running_experiment(db, bot_id=experiment.bot_id)
    if other is not None and other.id != experiment.id:
        raise ValidationError(
            f"Experiment {other.id} is already running for this bot; stop it first"
        )

    await _pin_control(db, experiment, list(variants))
    experiment.status = "running"
    experiment.started_at = datetime.now(UTC)
    await db.flush()
    return experiment


async def _pin_control(
    db: AsyncSession, experiment: AbExperiment, variants: list[AbVariant]
) -> None:
    """Fix what the control arm runs, and refuse an experiment with one arm.

    A control variant carries no version: it means "whatever the bot serves".
    That is a moving target - promoting a version, or restoring one in the
    editor, changes what control is measuring halfway through, and nothing
    says so. Worse, it can quietly become the candidate's own graph, and then
    the experiment produces two columns of the same numbers and a conclusion
    drawn from them.

    So the graph is resolved once, here, and both arms are compared before a
    single visitor is assigned.
    """
    control = next((v for v in variants if v.is_control), None)
    if control is None:
        return
    if control.pipeline_version_id is None:
        control.pipeline_version_id = await _bot_version_id(db, bot_id=experiment.bot_id)

    duplicate = next(
        (
            v
            for v in variants
            if not v.is_control and v.pipeline_version_id == control.pipeline_version_id
        ),
        None,
    )
    if duplicate is not None:
        raise ValidationError(
            f"Variant {duplicate.name!r} runs the same graph as the control; "
            "an experiment needs two different graphs to compare"
        )


async def _bot_version_id(db: AsyncSession, *, bot_id: int) -> int | None:
    """The pipeline version a bot serves, or None when it runs the built-in default."""
    from zk2.bots.models import Bot  # noqa: PLC0415  (cycle: bots -> ab)
    from zk2.pipelines.models import Pipeline  # noqa: PLC0415

    bot = await db.scalar(select(Bot).where(Bot.id == bot_id))
    if bot is None or bot.pipeline_id is None:
        return None
    pipeline = await db.scalar(select(Pipeline).where(Pipeline.id == bot.pipeline_id))
    return pipeline.current_version_id if pipeline is not None else None


async def stop(db: AsyncSession, experiment: AbExperiment) -> AbExperiment:
    experiment.status = "stopped"
    experiment.stopped_at = datetime.now(UTC)
    await db.flush()
    return experiment


_ANALYTICS_SQL = """
    SELECT metadata->>'variant_id'          AS variant_id,
           count(*)                         AS calls,
           coalesce(sum(tokens_in), 0)      AS tokens_in,
           coalesce(sum(tokens_out), 0)     AS tokens_out,
           coalesce(sum(cost_usd), 0)       AS cost_usd
    FROM usage_events
    WHERE org_id = :org AND metadata->>'experiment_id' = :experiment
    GROUP BY metadata->>'variant_id'
"""


async def analytics(
    db: AsyncSession, *, org_id: int, experiment: AbExperiment
) -> list[dict[str, object]]:
    """Traffic and spend per variant, from the usage events the turns wrote."""
    variants = {
        variant.id: variant
        for variant in (
            (await db.execute(select(AbVariant).where(AbVariant.experiment_id == experiment.id)))
            .scalars()
            .all()
        )
    }
    rows = (
        await db.execute(text(_ANALYTICS_SQL), {"org": org_id, "experiment": str(experiment.id)})
    ).all()
    by_variant = {int(row.variant_id): row for row in rows if row.variant_id}

    result: list[dict[str, object]] = []
    for variant_id, variant in variants.items():
        row = by_variant.get(variant_id)
        result.append(
            {
                "variant_id": variant_id,
                "name": variant.name,
                "is_control": variant.is_control,
                "traffic_percent": variant.traffic_percent,
                "calls": int(row.calls) if row else 0,
                "tokens_in": int(row.tokens_in) if row else 0,
                "tokens_out": int(row.tokens_out) if row else 0,
                "cost_usd": str(row.cost_usd) if row else "0",
            }
        )
    return result


async def require_experiment(db: AsyncSession, *, org_id: int, experiment_id: int) -> AbExperiment:
    experiment = await db.scalar(
        select(AbExperiment).where(AbExperiment.id == experiment_id, AbExperiment.org_id == org_id)
    )
    if experiment is None:
        raise NotFoundError("Experiment not found")
    return experiment
