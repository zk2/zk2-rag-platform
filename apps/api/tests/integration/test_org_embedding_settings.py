"""Embedding settings per organization, and the reindex a change implies."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from zk2.orgs.service import describe_embedding_settings, queue_reindex
from zk2.sources.models import Source, SourceStatus
from zk2.sources.service import create_file_source

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _provider(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _provider)


async def _ingest(db: AsyncSession, *, org_id: int, name: str = "doc.txt") -> Source:
    source = await create_file_source(
        db, None, org_id=org_id, parent_id=None, filename=name, content=b"alpha beta gamma"
    )
    await db.commit()
    return source


async def test_defaults_are_created_on_first_read(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    described = await describe_embedding_settings(db, org_id=org_owner["org_id"])
    assert described.embedding_provider == "openai"
    assert described.embedding_model == "text-embedding-3-small"
    assert described.dimensions == 1536
    assert described.indexed_sources == 0


async def test_settings_report_stale_sources_after_a_model_change(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """The whole point: the switch is visible instead of silently emptying search."""
    await _ingest(db, org_id=org_owner["org_id"])
    # Pretend the corpus was embedded with the model the org is configured for
    await db.execute(text("UPDATE source_embeddings SET model = 'text-embedding-3-small'"))
    await db.commit()

    before = await owner_client.get("/settings/embedding")
    assert before.status_code == 200
    assert before.json()["indexed_sources"] == 1
    assert before.json()["stale_sources"] == 0

    changed = await owner_client.put(
        "/settings/embedding",
        json={"embedding_provider": "openai", "embedding_model": "text-embedding-3-large"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["embedding_model"] == "text-embedding-3-large"
    assert body["stale_sources"] == 1


async def test_unknown_model_is_rejected(owner_client: AsyncClient) -> None:
    resp = await owner_client.put(
        "/settings/embedding",
        json={"embedding_provider": "openai", "embedding_model": "embed-9000"},
    )
    assert resp.status_code == 422
    assert "Unknown embedding model" in resp.json()["error"]["message"]


async def test_changing_settings_requires_admin(
    client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    from zk2.auth.models import Membership

    membership = await db.scalar(
        select(Membership).where(Membership.user_id == org_owner["user_id"])
    )
    assert membership is not None
    membership.role = "viewer"
    await db.commit()

    login = await client.post(
        "/auth/login", json={"email": org_owner["email"], "password": org_owner["password"]}
    )
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Org-Id": str(org_owner["org_id"]),
    }
    resp = await client.put(
        "/settings/embedding",
        json={"embedding_provider": "openai", "embedding_model": "text-embedding-3-large"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_reindex_marks_sources_pending(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    org_id = org_owner["org_id"]
    source = await _ingest(db, org_id=org_id)
    await db.refresh(source)
    assert source.status == SourceStatus.READY.value

    queued = await queue_reindex(db, None, org_id=org_id, only_stale=False)
    await db.commit()
    assert queued == 1
    # The inline fallback runs ingest immediately, so it is ready again
    await db.refresh(source)
    assert source.status == SourceStatus.READY.value


async def test_only_stale_skips_up_to_date_sources(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    org_id = org_owner["org_id"]
    await _ingest(db, org_id=org_id)
    # Indexed with the model the org is configured for
    await db.execute(text("UPDATE source_embeddings SET model = 'text-embedding-3-small'"))
    await db.commit()

    assert await queue_reindex(db, None, org_id=org_id, only_stale=True) == 0
    assert await queue_reindex(db, None, org_id=org_id, only_stale=False) == 1


async def test_reindex_endpoint_reports_what_it_queued(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    await _ingest(db, org_id=org_owner["org_id"])
    resp = await owner_client.post("/settings/embedding/reindex", params={"only_stale": False})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"queued": 1, "reason": "full_reindex"}


async def test_single_source_reindex_endpoint(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    source = await _ingest(db, org_id=org_owner["org_id"])
    resp = await owner_client.post(f"/sources/{source.id}/reindex")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == source.id


async def test_reindexing_a_directory_is_rejected(owner_client: AsyncClient) -> None:
    folder = (await owner_client.post("/sources/directory", json={"name": "Docs"})).json()
    resp = await owner_client.post(f"/sources/{folder['id']}/reindex")
    assert resp.status_code == 422
    assert "directory" in resp.json()["error"]["message"].lower()


async def test_chunking_settings_are_returned_with_the_embedding_ones(
    owner_client: AsyncClient,
) -> None:
    body = (await owner_client.get("/settings/embedding")).json()
    assert body["chunk_size"] == 400
    assert body["chunk_overlap"] == 40
    assert body["chunk_strategy"] == "semantic"


async def test_changing_chunking_makes_the_corpus_stale(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """Cutting documents differently invalidates the index exactly as a new model does."""
    await _ingest(db, org_id=org_owner["org_id"])
    await db.execute(text("UPDATE source_embeddings SET model = 'text-embedding-3-small'"))
    await db.commit()
    assert (await owner_client.get("/settings/embedding")).json()["stale_sources"] == 0

    changed = await owner_client.put(
        "/settings/chunking",
        json={"chunk_size": 800, "chunk_overlap": 80, "chunk_strategy": "semantic"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["chunk_size"] == 800
    assert changed.json()["stale_sources"] == 1

    assert await queue_reindex(db, None, org_id=org_owner["org_id"], only_stale=True) == 1


async def test_overlap_must_be_smaller_than_the_chunk(owner_client: AsyncClient) -> None:
    resp = await owner_client.put(
        "/settings/chunking",
        json={"chunk_size": 400, "chunk_overlap": 400, "chunk_strategy": "semantic"},
    )
    assert resp.status_code == 422


async def test_an_unknown_strategy_is_rejected(owner_client: AsyncClient) -> None:
    resp = await owner_client.put(
        "/settings/chunking",
        json={"chunk_size": 400, "chunk_overlap": 40, "chunk_strategy": "vibes"},
    )
    assert resp.status_code == 422
