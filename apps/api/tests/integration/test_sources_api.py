"""Sources API: tree shape, upload limits, sitemap preview."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings

pytestmark = pytest.mark.integration


async def test_directory_tree_is_nested(owner_client: AsyncClient) -> None:
    root = await owner_client.post("/sources/directory", json={"name": "Docs"})
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]

    child = await owner_client.post(
        "/sources/directory", json={"name": "Manuals", "parent_id": root_id}
    )
    assert child.status_code == 201

    tree = await owner_client.get("/sources/tree")
    assert tree.status_code == 200
    nodes = tree.json()
    assert len(nodes) == 1
    assert nodes[0]["name"] == "Docs"
    assert [c["name"] for c in nodes[0]["children"]] == ["Manuals"]


async def test_subtree_query_returns_only_descendants(owner_client: AsyncClient) -> None:
    a = (await owner_client.post("/sources/directory", json={"name": "A"})).json()
    await owner_client.post("/sources/directory", json={"name": "B"})
    await owner_client.post("/sources/directory", json={"name": "A1", "parent_id": a["id"]})

    subtree = await owner_client.get("/sources/tree", params={"parent_id": a["id"]})
    assert [n["name"] for n in subtree.json()] == ["A1"]


async def test_delete_removes_whole_subtree(owner_client: AsyncClient) -> None:
    parent = (await owner_client.post("/sources/directory", json={"name": "Parent"})).json()
    await owner_client.post("/sources/directory", json={"name": "Child", "parent_id": parent["id"]})

    deleted = await owner_client.delete(f"/sources/{parent['id']}")
    assert deleted.status_code == 200
    assert (await owner_client.get("/sources/tree")).json() == []


async def test_upload_rejects_unsupported_extension(owner_client: AsyncClient) -> None:
    resp = await owner_client.post(
        "/sources/file", files={"file": ("payload.exe", b"MZ...", "application/octet-stream")}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"


async def test_upload_rejects_oversized_file(
    owner_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().ingest, "max_upload_bytes", 32)
    resp = await owner_client.post(
        "/sources/file", files={"file": ("big.txt", b"x" * 1024, "text/plain")}
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


async def test_upload_rejects_empty_file(owner_client: AsyncClient) -> None:
    resp = await owner_client.post(
        "/sources/file", files={"file": ("empty.txt", b"", "text/plain")}
    )
    assert resp.status_code == 422


async def test_upload_accepts_text_file(owner_client: AsyncClient) -> None:
    resp = await owner_client.post(
        "/sources/file", files={"file": ("notes.txt", b"hello there", "text/plain")}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "notes.txt"
    assert body["status"] in {"pending", "indexing", "ready"}


async def test_url_source_rejects_private_address(owner_client: AsyncClient) -> None:
    """A source URL pointing at localhost must never be queued for fetching."""
    resp = await owner_client.post("/sources/url", json={"url": "http://127.0.0.1:8000/secret"})
    assert resp.status_code in {422, 400}
    assert resp.json()["error"]["code"] in {"unsafe_url", "validation_failed"}


async def test_endpoints_require_org_header(client: AsyncClient, org_owner: dict[str, Any]) -> None:
    login = await client.post(
        "/auth/login", json={"email": org_owner["email"], "password": org_owner["password"]}
    )
    token = login.json()["access_token"]
    resp = await client.get("/sources/tree", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_sitemap_preview_reports_volume_before_import(
    owner_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview exists so nobody queues 500 embedding jobs by accident."""
    urls = [f"https://example.com/page-{i}" for i in range(150)]

    async def _discover(*_args: object, **_kwargs: object) -> list[str]:
        return urls

    monkeypatch.setattr("zk2.sources.router.discover_sitemap", _discover)

    resp = await owner_client.post(
        "/sources/sitemap/preview", json={"base_url": "https://example.com", "limit": 100}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_found"] == 150
    assert body["would_import"] == 100
    assert len(body["urls"]) == 20


async def test_sitemap_import_creates_sources(
    owner_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _discover(*_args: object, **_kwargs: object) -> list[str]:
        return ["https://example.com/a", "https://example.com/b"]

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("zk2.sources.service.discover_sitemap", _discover)
    monkeypatch.setattr("zk2.sources.service.assert_url_allowed", _allow)

    resp = await owner_client.post(
        "/sources/sitemap", json={"base_url": "https://example.com", "limit": 10}
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()) == 2
    tree = (await owner_client.get("/sources/tree")).json()
    assert {n["name"] for n in tree} == {"https://example.com/a", "https://example.com/b"}


async def test_a_failed_document_can_be_indexed_again_and_says_why_it_failed(
    owner_client: AsyncClient, db: AsyncSession
) -> None:
    """Indexing fails whenever a provider key is missing or spent. The tree has
    to carry the reason, and there has to be a way back - otherwise a document
    is stuck with nothing but the word FAILED next to it."""
    upload = await owner_client.post(
        "/sources/file", files={"file": ("notes.txt", b"hello there", "text/plain")}
    )
    source_id = upload.json()["id"]
    await db.execute(
        text("UPDATE sources SET status = 'failed', error = :e WHERE id = :id"),
        {"e": "openai: You have no credits remaining.", "id": source_id},
    )
    await db.commit()

    node = (await owner_client.get("/sources/tree")).json()[0]
    assert node["status"] == "failed"
    assert node["error"] == "openai: You have no credits remaining."

    again = await owner_client.post(f"/sources/{source_id}/reindex")
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "pending"

    node = (await owner_client.get("/sources/tree")).json()[0]
    assert node["error"] is None, "a queued retry clears the old failure"


async def test_a_directory_cannot_be_indexed(owner_client: AsyncClient) -> None:
    folder = await owner_client.post("/sources/directory", json={"name": "Docs"})
    resp = await owner_client.post(f"/sources/{folder.json()['id']}/reindex")
    assert resp.status_code == 422
    assert "nothing to index" in resp.json()["error"]["message"]
