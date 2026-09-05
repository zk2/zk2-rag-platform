"""The shared-key allowance: what the deployment lends, and when it stops."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings
from zk2.core.quota import KeySource, SystemKeyExhaustedError, allowance, ensure_system_key_allowed
from zk2.core.security import encrypt
from zk2.llm.models import LLMProviderConfig
from zk2.llm.registry import key_source_for, require_key

pytestmark = pytest.mark.integration


@pytest.fixture
def system_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment has an OpenAI key of its own."""
    monkeypatch.setattr(get_settings().llm, "openai_api_key", SecretStr("sk-deployment"))


async def _spend(db: AsyncSession, org_id: int, tokens: int, source: str = "system") -> None:
    await db.execute(
        text(
            "INSERT INTO usage_events (org_id, event_type, provider, model, tokens_in,"
            " tokens_out, cost_usd, key_source) VALUES (:org, 'llm_call', 'openai',"
            " 'gpt-4.1-mini', :tokens, 0, 0.0001, :source)"
        ),
        {"org": org_id, "tokens": tokens, "source": source},
    )
    await db.commit()
    # The cached counter is seeded from the database on the next read
    from zk2.core.redis_client import get_redis

    await get_redis().delete(f"quota:system-tokens:{org_id}")


async def test_a_fresh_org_uses_the_deployment_key(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None
) -> None:
    resolved = await require_key(db, org_owner["org_id"], "openai")
    assert resolved.source is KeySource.SYSTEM
    assert resolved.value == "sk-deployment"


async def test_an_org_key_wins_over_the_deployment_key(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None
) -> None:
    db.add(
        LLMProviderConfig(
            org_id=org_owner["org_id"], provider="openai", api_key_encrypted=encrypt("sk-own")
        )
    )
    await db.commit()

    resolved = await require_key(db, org_owner["org_id"], "openai")
    assert resolved.source is KeySource.ORG
    assert resolved.value == "sk-own"


async def test_the_allowance_counts_only_system_key_usage(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None
) -> None:
    await _spend(db, org_owner["org_id"], 1000, source="system")
    await _spend(db, org_owner["org_id"], 5000, source="org")

    current = await allowance(db, org_id=org_owner["org_id"])
    assert current.used == 1000
    assert current.remaining == current.limit - 1000
    assert current.exhausted is False


async def test_exhausting_the_allowance_blocks_the_shared_key(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().quota, "system_key_token_limit", 500)
    await _spend(db, org_owner["org_id"], 600)

    with pytest.raises(SystemKeyExhaustedError, match="allowance"):
        await require_key(db, org_owner["org_id"], "openai")


async def test_an_own_key_keeps_working_past_the_allowance(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit is on lending someone else's key, not on using the product."""
    monkeypatch.setattr(get_settings().quota, "system_key_token_limit", 500)
    await _spend(db, org_owner["org_id"], 600)
    db.add(
        LLMProviderConfig(
            org_id=org_owner["org_id"], provider="openai", api_key_encrypted=encrypt("sk-own")
        )
    )
    await db.commit()

    resolved = await require_key(db, org_owner["org_id"], "openai")
    assert resolved.source is KeySource.ORG


async def test_disabling_shared_keys_refuses_immediately(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().quota, "system_keys_enabled", False)
    with pytest.raises(SystemKeyExhaustedError, match="does not lend"):
        await ensure_system_key_allowed(db, org_id=org_owner["org_id"], provider="openai")


async def test_key_source_reports_what_a_call_would_use(
    db: AsyncSession, org_owner: dict[str, Any], system_key: None
) -> None:
    assert (
        await key_source_for(db, org_id=org_owner["org_id"], provider="openai") is KeySource.SYSTEM
    )
    db.add(
        LLMProviderConfig(
            org_id=org_owner["org_id"], provider="openai", api_key_encrypted=encrypt("sk-own")
        )
    )
    await db.commit()
    assert await key_source_for(db, org_id=org_owner["org_id"], provider="openai") is KeySource.ORG


async def test_the_allowance_endpoint_reports_the_remainder(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], system_key: None
) -> None:
    await _spend(db, org_owner["org_id"], 1200)
    body = (await owner_client.get("/observability/allowance")).json()
    assert body["used_tokens"] == 1200
    assert body["remaining_tokens"] == body["limit_tokens"] - 1200
    assert body["exhausted"] is False
    assert body["own_keys"] == []


async def test_the_endpoint_lists_providers_with_their_own_key(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    db.add(
        LLMProviderConfig(
            org_id=org_owner["org_id"], provider="anthropic", api_key_encrypted=encrypt("sk-own")
        )
    )
    await db.commit()
    body = (await owner_client.get("/observability/allowance")).json()
    assert body["own_keys"] == ["anthropic"]


async def test_a_blocked_turn_explains_what_to_do(
    owner_client: AsyncClient,
    db: AsyncSession,
    org_owner: dict[str, Any],
    system_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error names the fix rather than saying 'forbidden'."""
    monkeypatch.setattr(get_settings().quota, "system_key_token_limit", 100)
    await _spend(db, org_owner["org_id"], 500)

    bot = await owner_client.post("/bots", json={"name": "Helper", "llm_model": "gpt-4.1-mini"})
    upload = await owner_client.post(
        "/sources/file", files={"file": ("a.txt", b"alpha", "text/plain")}
    )
    assert bot.status_code == 201
    # Ingest resolves an embedding key, which is where the refusal surfaces
    from zk2.sources.ingest import ingest_source

    await ingest_source(db, source_id=upload.json()["id"])
    await db.commit()

    tree = (await owner_client.get("/sources/tree")).json()
    failed = [n for n in tree if n["status"] == "failed"]
    assert failed, tree
