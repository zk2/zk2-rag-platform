"""Resolving a provider for an organization: keys, base URLs, failures."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.errors import ValidationError
from zk2.core.security import encrypt
from zk2.llm.anthropic_provider import AnthropicProvider
from zk2.llm.gemini_provider import GeminiProvider
from zk2.llm.models import LLMProviderConfig
from zk2.llm.ollama_provider import OllamaProvider
from zk2.llm.openai_provider import OpenAIProvider
from zk2.llm.registry import get_llm_provider

pytestmark = pytest.mark.integration


async def _store_key(db: AsyncSession, *, org_id: int, provider: str, key: str = "sk-test") -> None:
    db.add(LLMProviderConfig(org_id=org_id, provider=provider, api_key_encrypted=encrypt(key)))
    await db.commit()


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", OpenAIProvider),
        ("anthropic", AnthropicProvider),
        ("gemini", GeminiProvider),
    ],
)
async def test_key_providers_resolve_with_a_stored_key(
    db: AsyncSession, org_owner: dict[str, Any], provider: str, expected: type
) -> None:
    await _store_key(db, org_id=org_owner["org_id"], provider=provider)
    resolved = await get_llm_provider(db, org_id=org_owner["org_id"], provider=provider)
    assert isinstance(resolved, expected)
    assert resolved.name == provider


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
async def test_missing_key_is_a_clear_error(
    db: AsyncSession, org_owner: dict[str, Any], provider: str
) -> None:
    with pytest.raises(ValidationError, match="API key not configured"):
        await get_llm_provider(db, org_id=org_owner["org_id"], provider=provider)


async def test_ollama_needs_no_key(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    """A local model has no key - the deployment default base URL is enough."""
    resolved = await get_llm_provider(db, org_id=org_owner["org_id"], provider="ollama")
    assert isinstance(resolved, OllamaProvider)


async def test_ollama_prefers_the_org_base_url(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    db.add(
        LLMProviderConfig(
            org_id=org_owner["org_id"],
            provider="ollama",
            custom_base_url="http://gpu-box.internal:11434",
        )
    )
    await db.commit()
    resolved = await get_llm_provider(db, org_id=org_owner["org_id"], provider="ollama")
    assert isinstance(resolved, OllamaProvider)
    assert resolved._base_url == "http://gpu-box.internal:11434"


async def test_keys_are_scoped_to_the_organization(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    await _store_key(db, org_id=org_owner["org_id"], provider="anthropic")
    with pytest.raises(ValidationError):
        await get_llm_provider(db, org_id=org_owner["org_id"] + 999, provider="anthropic")


async def test_unknown_provider_is_rejected(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="Unknown LLM provider"):
        await get_llm_provider(db, org_id=org_owner["org_id"], provider="skynet")
