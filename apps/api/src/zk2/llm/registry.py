"""Resolve LLM/embedding providers for an organization.

Lookup order for API keys:
1. Org-scoped key in llm_providers table (encrypted)
2. Process-wide fallback from env / settings
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings
from zk2.core.errors import ValidationFailed
from zk2.core.security import decrypt
from zk2.llm.base import EmbeddingProvider, LLMProvider
from zk2.llm.models import LLMProviderConfig
from zk2.llm.openai_provider import OpenAIEmbeddings, OpenAIProvider


async def _get_api_key(db: AsyncSession, org_id: int, provider: str) -> str | None:
    row = await db.scalar(
        select(LLMProviderConfig).where(
            LLMProviderConfig.org_id == org_id,
            LLMProviderConfig.provider == provider,
        )
    )
    if row is not None and row.api_key_encrypted:
        return decrypt(row.api_key_encrypted)
    settings = get_settings().llm
    fallback = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }.get(provider)
    return fallback.get_secret_value() if fallback else None


async def get_llm_provider(
    db: AsyncSession, *, org_id: int, provider: str
) -> LLMProvider:
    if provider == "openai":
        key = await _get_api_key(db, org_id, "openai")
        if not key:
            raise ValidationFailed("OpenAI API key not configured for this org")
        return OpenAIProvider(api_key=key)
    msg = f"LLM provider {provider!r} not yet supported"
    raise ValidationFailed(msg)


async def get_embedding_provider(
    db: AsyncSession,
    *,
    org_id: int,
    provider: str = "openai",
    model: str = "text-embedding-3-small",
) -> EmbeddingProvider:
    if provider == "openai":
        key = await _get_api_key(db, org_id, "openai")
        if not key:
            raise ValidationFailed("OpenAI API key not configured for this org")
        return OpenAIEmbeddings(api_key=key, model=model)
    msg = f"Embedding provider {provider!r} not yet supported"
    raise ValidationFailed(msg)
