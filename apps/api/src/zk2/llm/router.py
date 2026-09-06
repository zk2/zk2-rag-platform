"""Org-scoped LLM provider settings (encrypted API keys)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.config import get_settings
from zk2.core.audit import write_audit
from zk2.core.deps import get_db_dep
from zk2.core.errors import ValidationError
from zk2.core.security import encrypt
from zk2.llm.catalog import Catalog, EmbeddingModel, get_catalog
from zk2.llm.models import LLMProviderConfig
from zk2.llm.registry import embedding_model_support
from zk2.llm.schemas import ProviderDto, ProviderUpsert

SUPPORTED_PROVIDERS = {"openai", "anthropic", "gemini", "ollama"}

router = APIRouter(prefix="/settings/providers", tags=["settings"])
models_router = APIRouter(prefix="/settings/models", tags=["settings"])


def _with_support(model: EmbeddingModel) -> EmbeddingModel:
    reason = embedding_model_support(model)
    return model.model_copy(update={"usable": reason is None, "unusable_reason": reason})


@models_router.get("", response_model=Catalog)
async def list_models(
    _ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
) -> Catalog:
    """The model catalog: what the UI offers and what cost accounting uses.

    The yaml lists models the platform knows how to price. Whether a given
    embedding model can be selected is a property of this deployment, so it is
    answered here rather than baked into the file.
    """
    catalog = get_catalog()
    return catalog.model_copy(
        update={
            "embedding": [_with_support(m) for m in catalog.embedding],
            "embedding_dimensions": get_settings().ingest.embedding_dimensions,
        }
    )


def _to_dto(row: LLMProviderConfig) -> ProviderDto:
    return ProviderDto(
        id=row.id,
        provider=row.provider,
        has_key=bool(row.api_key_encrypted),
        custom_base_url=row.custom_base_url,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[ProviderDto])
async def list_providers(
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ProviderDto]:
    rows = (
        (
            await db.execute(
                select(LLMProviderConfig)
                .where(LLMProviderConfig.org_id == ctx.org_id)
                .order_by(LLMProviderConfig.provider)
            )
        )
        .scalars()
        .all()
    )
    return [_to_dto(r) for r in rows]


@router.put("/{provider}", response_model=ProviderDto)
async def upsert_provider(
    provider: str,
    payload: ProviderUpsert,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ProviderDto:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValidationError(f"Unknown provider: {provider}")

    row = await db.scalar(
        select(LLMProviderConfig).where(
            LLMProviderConfig.org_id == ctx.org_id,
            LLMProviderConfig.provider == provider,
        )
    )
    if row is None:
        row = LLMProviderConfig(org_id=ctx.org_id, provider=provider)
        db.add(row)

    if payload.api_key is not None:
        row.api_key_encrypted = encrypt(payload.api_key)
    if payload.custom_base_url is not None:
        row.custom_base_url = payload.custom_base_url or None
    row.updated_at = datetime.now(UTC)
    await db.flush()

    await write_audit(
        db,
        action="provider.upserted",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=provider,
        payload={"has_key_now": bool(row.api_key_encrypted)},
    )
    return _to_dto(row)
