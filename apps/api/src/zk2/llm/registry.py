"""Resolve LLM/embedding providers for an organization.

Lookup order for API keys:
1. Org-scoped key in llm_providers table (encrypted)
2. Process-wide fallback from env / settings

Local providers (Ollama) need a base URL rather than a key: per-org first, then
the deployment default.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings
from zk2.core.errors import ValidationError
from zk2.core.quota import KeySource, ensure_system_key_allowed
from zk2.core.security import decrypt
from zk2.llm.anthropic_provider import AnthropicProvider
from zk2.llm.base import EmbeddingProvider, LLMProvider
from zk2.llm.gemini_provider import GeminiProvider
from zk2.llm.models import LLMProviderConfig
from zk2.llm.ollama_provider import OllamaProvider
from zk2.llm.openai_provider import OpenAIEmbeddings, OpenAIProvider

KEY_PROVIDERS = ("openai", "anthropic", "gemini")


async def _get_config(db: AsyncSession, org_id: int, provider: str) -> LLMProviderConfig | None:
    row: LLMProviderConfig | None = await db.scalar(
        select(LLMProviderConfig).where(
            LLMProviderConfig.org_id == org_id,
            LLMProviderConfig.provider == provider,
        )
    )
    return row


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """A usable key and whose it is - the allowance depends on the difference."""

    value: str
    source: KeySource


async def _resolve_key(db: AsyncSession, org_id: int, provider: str) -> ResolvedKey | None:
    """The organization's own key if it has one, otherwise the deployment's."""
    row = await _get_config(db, org_id, provider)
    if row is not None and row.api_key_encrypted:
        return ResolvedKey(decrypt(row.api_key_encrypted), KeySource.ORG)

    settings = get_settings().llm
    fallback = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }.get(provider)
    if fallback is None:
        return None
    return ResolvedKey(fallback.get_secret_value(), KeySource.SYSTEM)


async def require_api_key(db: AsyncSession, *, org_id: int, provider: str) -> str:
    """Public accessor: the agent layer needs a key without building a provider."""
    return (await require_key(db, org_id, provider)).value


async def require_key(db: AsyncSession, org_id: int, provider: str) -> ResolvedKey:
    """Resolve a key, refusing a shared one once the allowance is spent."""
    resolved = await _resolve_key(db, org_id, provider)
    if resolved is None:
        raise ValidationError(f"{provider} API key not configured for this organization")
    if resolved.source is KeySource.SYSTEM:
        await ensure_system_key_allowed(db, org_id=org_id, provider=provider)
    return resolved


async def key_source_for(db: AsyncSession, *, org_id: int, provider: str) -> KeySource:
    """Whose key a call to this provider would use right now."""
    resolved = await _resolve_key(db, org_id, provider)
    return resolved.source if resolved else KeySource.ORG


async def _ollama_base_url(db: AsyncSession, org_id: int) -> str:
    row = await _get_config(db, org_id, "ollama")
    if row is not None and row.custom_base_url:
        return row.custom_base_url
    return get_settings().llm.ollama_base_url


async def get_llm_provider(db: AsyncSession, *, org_id: int, provider: str) -> LLMProvider:
    if provider == "openai":
        row = await _get_config(db, org_id, "openai")
        return OpenAIProvider(
            api_key=(await require_key(db, org_id, "openai")).value,
            base_url=row.custom_base_url if row else None,
        )
    if provider == "anthropic":
        row = await _get_config(db, org_id, "anthropic")
        return AnthropicProvider(
            api_key=(await require_key(db, org_id, "anthropic")).value,
            base_url=row.custom_base_url if row else None,
        )
    if provider == "gemini":
        return GeminiProvider(api_key=(await require_key(db, org_id, "gemini")).value)
    if provider == "ollama":
        return OllamaProvider(base_url=await _ollama_base_url(db, org_id))
    raise ValidationError(f"Unknown LLM provider: {provider!r}")


async def get_embedding_provider(
    db: AsyncSession,
    *,
    org_id: int,
    provider: str | None = None,
    model: str | None = None,
) -> EmbeddingProvider:
    """Embedding provider for an organization.

    Provider and model come from the organization's settings unless the caller
    overrides them - the ingest path and the query path must agree, and a
    constant in the code is how they stopped agreeing before.
    """
    from zk2.orgs.service import get_org_settings  # noqa: PLC0415  (cycle: orgs -> llm)

    if provider is None or model is None:
        settings = await get_org_settings(db, org_id=org_id)
        provider = provider or settings.embedding_provider
        model = model or settings.embedding_model

    if provider == "openai":
        return OpenAIEmbeddings(
            api_key=(await require_key(db, org_id, "openai")).value, model=model
        )
    msg = f"Embedding provider {provider!r} not yet supported"
    raise ValidationError(msg)
