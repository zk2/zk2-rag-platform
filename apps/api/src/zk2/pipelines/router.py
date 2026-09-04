"""Pipeline editor API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.core.audit import write_audit
from zk2.core.deps import get_db_dep
from zk2.pipelines.models import Pipeline, PipelineVersion
from zk2.pipelines.nodes import node_types
from zk2.pipelines.schemas import (
    DagSave,
    NodeTypeDto,
    PipelineCreate,
    PipelineDetailDto,
    PipelineDto,
    PipelinePatch,
    PipelineVersionDetailDto,
    PipelineVersionDto,
    TestRunDto,
    TestRunRequest,
)
from zk2.pipelines.service import (
    activate_version,
    create_pipeline,
    delete_pipeline,
    describe,
    save_version,
    test_run,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("/node-types", response_model=list[NodeTypeDto])
async def list_node_types(
    _ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
) -> list[NodeTypeDto]:
    """The editor palette: one entry per node type, with its config schema."""
    return [
        NodeTypeDto(
            type=entry.type,
            title=entry.title,
            description=entry.description,
            config_schema=entry.config_schema,
        )
        for entry in node_types()
    ]


@router.get("", response_model=list[PipelineDto])
async def list_pipelines(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[PipelineDto]:
    rows = (
        (
            await db.execute(
                select(Pipeline)
                .where(Pipeline.org_id == ctx.org_id)
                .order_by(Pipeline.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [PipelineDto.model_validate(row) for row in rows]


@router.post("", response_model=PipelineDetailDto, status_code=status.HTTP_201_CREATED)
async def create_pipeline_endpoint(
    payload: PipelineCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineDetailDto:
    pipeline = await create_pipeline(
        db,
        org_id=ctx.org_id,
        user=ctx.user,
        name=payload.name,
        description=payload.description,
        dag=payload.dag,
    )
    return await describe(db, pipeline)


@router.get("/{pipeline_id}", response_model=PipelineDetailDto)
async def get_pipeline(
    pipeline_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineDetailDto:
    from zk2.pipelines.service import _require_pipeline  # noqa: PLC0415

    pipeline = await _require_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    return await describe(db, pipeline)


@router.patch("/{pipeline_id}", response_model=PipelineDetailDto)
async def patch_pipeline(
    pipeline_id: int,
    payload: PipelinePatch,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineDetailDto:
    from zk2.pipelines.service import _require_pipeline  # noqa: PLC0415

    pipeline = await _require_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    if payload.name is not None:
        pipeline.name = payload.name
    if payload.description is not None:
        pipeline.description = payload.description
    await db.flush()
    return await describe(db, pipeline)


@router.delete("/{pipeline_id}")
async def delete_pipeline_endpoint(
    pipeline_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, str]:
    await delete_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    return {"status": "deleted"}


@router.put("/{pipeline_id}/dag", response_model=PipelineVersionDetailDto)
async def save_dag(
    pipeline_id: int,
    payload: DagSave,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineVersionDetailDto:
    """Save the graph. Always a new version - the previous one stays reachable."""
    version = await save_version(
        db,
        org_id=ctx.org_id,
        pipeline_id=pipeline_id,
        user=ctx.user,
        dag=payload.dag,
        label=payload.label,
    )
    await write_audit(
        db,
        action="pipeline.version.saved",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=str(pipeline_id),
        payload={"version_id": version.id, "nodes": len(payload.dag.nodes)},
    )
    return PipelineVersionDetailDto(
        id=version.id,
        pipeline_id=version.pipeline_id,
        label=version.label,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
        is_current=True,
        dag=payload.dag,
    )


@router.get("/{pipeline_id}/versions", response_model=list[PipelineVersionDto])
async def list_versions(
    pipeline_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[PipelineVersionDto]:
    from zk2.pipelines.service import _require_pipeline  # noqa: PLC0415

    pipeline = await _require_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    rows = (
        (
            await db.execute(
                select(PipelineVersion)
                .where(PipelineVersion.pipeline_id == pipeline.id)
                .order_by(PipelineVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        PipelineVersionDto(
            id=row.id,
            pipeline_id=row.pipeline_id,
            label=row.label,
            created_by_user_id=row.created_by_user_id,
            created_at=row.created_at,
            is_current=row.id == pipeline.current_version_id,
        )
        for row in rows
    ]


@router.get("/{pipeline_id}/versions/{version_id}", response_model=PipelineVersionDetailDto)
async def get_version(
    pipeline_id: int,
    version_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineVersionDetailDto:
    from zk2.core.errors import NotFoundError  # noqa: PLC0415
    from zk2.pipelines.dag import DagSpec  # noqa: PLC0415
    from zk2.pipelines.service import _require_pipeline  # noqa: PLC0415

    pipeline = await _require_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    row = await db.scalar(
        select(PipelineVersion).where(
            PipelineVersion.id == version_id, PipelineVersion.pipeline_id == pipeline.id
        )
    )
    if row is None:
        raise NotFoundError("Version not found")
    return PipelineVersionDetailDto(
        id=row.id,
        pipeline_id=row.pipeline_id,
        label=row.label,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        is_current=row.id == pipeline.current_version_id,
        dag=DagSpec.model_validate(row.dag),
    )


@router.post("/{pipeline_id}/versions/{version_id}/activate", response_model=PipelineDetailDto)
async def activate(
    pipeline_id: int,
    version_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> PipelineDetailDto:
    """Roll back to a previous version."""
    from zk2.pipelines.service import _require_pipeline  # noqa: PLC0415

    await activate_version(db, org_id=ctx.org_id, pipeline_id=pipeline_id, version_id=version_id)
    await write_audit(
        db,
        action="pipeline.version.activated",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=str(pipeline_id),
        payload={"version_id": version_id},
    )
    pipeline = await _require_pipeline(db, org_id=ctx.org_id, pipeline_id=pipeline_id)
    return await describe(db, pipeline)


@router.post("/{pipeline_id}/test", response_model=TestRunDto)
async def test_pipeline(
    pipeline_id: int,
    payload: TestRunRequest,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> TestRunDto:
    """Run a question through the graph - the saved one, or an unsaved draft."""
    return await test_run(
        db,
        org_id=ctx.org_id,
        pipeline_id=pipeline_id,
        bot_id=payload.bot_id,
        question=payload.question,
        dag=payload.dag,
    )
