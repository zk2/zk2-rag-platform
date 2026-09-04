"""The pipeline editor API: versions, rollback, validation, test runs."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from tests.integration.test_rag_stream import ANSWER_PARTS, FakeLLM

pytestmark = pytest.mark.integration

MINIMAL_DAG = {
    "nodes": [
        {"id": "bm25", "type": "retriever_bm25", "config": {"k": 5}},
        {"id": "fuse", "type": "fusion", "config": {"limit": 5}},
        {"id": "context", "type": "context_builder", "config": {"token_budget": 1000}},
        {"id": "answer", "type": "generate", "config": {}},
    ],
    "edges": [["bm25", "fuse"], ["fuse", "context"], ["context", "answer"]],
}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    llm = FakeLLM()

    async def _get_llm(*_args: object, **_kwargs: object) -> FakeLLM:
        return llm

    async def _get_embeddings(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.pipelines.nodes.get_llm_provider", _get_llm)
    monkeypatch.setattr("zk2.pipelines.nodes.get_embedding_provider", _get_embeddings)
    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _get_embeddings)
    return llm


async def _create(client: AsyncClient, **kwargs: Any) -> dict[str, Any]:
    resp = await client.post("/pipelines", json={"name": "Support RAG", **kwargs})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_palette_lists_node_types_with_schemas(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/pipelines/node-types")
    assert resp.status_code == 200
    types = {entry["type"]: entry for entry in resp.json()}
    assert "generate" in types and "retriever_dense" in types
    assert types["retriever_dense"]["config_schema"]["properties"]["k"]["type"] == "integer"


async def test_a_new_pipeline_starts_from_the_default(owner_client: AsyncClient) -> None:
    body = await _create(owner_client)
    node_types = {n["type"] for n in body["dag"]["nodes"]}
    assert node_types == {
        "retriever_dense",
        "retriever_bm25",
        "fusion",
        "rerank",
        "context_builder",
        "generate",
    }
    assert body["current_version_id"] is not None
    assert body["version_count"] == 1


async def test_saving_creates_a_version_and_keeps_the_old_one(
    owner_client: AsyncClient,
) -> None:
    created = await _create(owner_client)
    first_version = created["current_version_id"]

    saved = await owner_client.put(
        f"/pipelines/{created['id']}/dag", json={"dag": MINIMAL_DAG, "label": "lexical only"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] != first_version

    versions = (await owner_client.get(f"/pipelines/{created['id']}/versions")).json()
    assert len(versions) == 2
    assert [v["is_current"] for v in versions].count(True) == 1
    assert versions[0]["label"] == "lexical only"


async def test_rollback_is_a_pointer_move(owner_client: AsyncClient) -> None:
    created = await _create(owner_client)
    original = created["current_version_id"]
    await owner_client.put(f"/pipelines/{created['id']}/dag", json={"dag": MINIMAL_DAG})

    rolled_back = await owner_client.post(
        f"/pipelines/{created['id']}/versions/{original}/activate"
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["current_version_id"] == original
    # Nothing was deleted by rolling back
    assert rolled_back.json()["version_count"] == 2


async def test_an_invalid_graph_is_refused_before_it_is_saved(
    owner_client: AsyncClient,
) -> None:
    created = await _create(owner_client)
    cyclic = {
        "nodes": [
            {"id": "a", "type": "fusion", "config": {}},
            {"id": "b", "type": "rerank", "config": {}},
            {"id": "g", "type": "generate", "config": {}},
        ],
        "edges": [["a", "b"], ["b", "a"], ["b", "g"]],
    }
    resp = await owner_client.put(f"/pipelines/{created['id']}/dag", json={"dag": cyclic})
    assert resp.status_code == 422
    assert "cycle" in resp.json()["error"]["message"]

    versions = (await owner_client.get(f"/pipelines/{created['id']}/versions")).json()
    assert len(versions) == 1, "a rejected graph must not create a version"


async def test_a_pipeline_in_use_cannot_be_deleted(
    owner_client: AsyncClient, fakes: FakeLLM
) -> None:
    created = await _create(owner_client)
    bot = await owner_client.post(
        "/bots", json={"name": "Helper", "pipeline_id": created["id"], "llm_model": "gpt-4.1-mini"}
    )
    assert bot.status_code == 201, bot.text

    refused = await owner_client.delete(f"/pipelines/{created['id']}")
    assert refused.status_code == 422
    assert "still use this pipeline" in refused.json()["error"]["message"]

    detached = await owner_client.patch(f"/bots/{bot.json()['id']}", json={"pipeline_id": None})
    assert detached.status_code == 200
    assert detached.json()["pipeline_id"] is None
    assert (await owner_client.delete(f"/pipelines/{created['id']}")).status_code == 200


async def test_test_run_reports_the_answer_and_per_node_timings(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    upload = await owner_client.post(
        "/sources/file", files={"file": ("alpha.txt", b"alpha alpha documented", "text/plain")}
    )
    assert upload.status_code == 201
    from zk2.sources.ingest import ingest_source

    await ingest_source(db, source_id=upload.json()["id"])
    await db.commit()

    created = await _create(owner_client)
    bot = (
        await owner_client.post(
            "/bots",
            json={
                "name": "Helper",
                "llm_model": "gpt-4.1-mini",
                "source_ids": [upload.json()["id"]],
            },
        )
    ).json()

    resp = await owner_client.post(
        f"/pipelines/{created['id']}/test",
        json={"bot_id": bot["id"], "question": "what about alpha?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["answer"] == "".join(ANSWER_PARTS)
    assert [n["node_id"] for n in body["nodes"]][-1] == "answer"
    assert body["duration_ms"] >= 0

    runs = (await db.execute(text("SELECT status, question FROM pipeline_runs"))).all()
    assert len(runs) == 1
    assert runs[0].status == "ok"


async def test_test_run_can_try_an_unsaved_draft(owner_client: AsyncClient, fakes: FakeLLM) -> None:
    """The editor must be able to try a change before committing it."""
    created = await _create(owner_client)
    bot = (
        await owner_client.post("/bots", json={"name": "Helper", "llm_model": "gpt-4.1-mini"})
    ).json()

    resp = await owner_client.post(
        f"/pipelines/{created['id']}/test",
        json={"bot_id": bot["id"], "question": "hello", "dag": MINIMAL_DAG},
    )
    assert resp.status_code == 200, resp.text
    assert [n["type"] for n in resp.json()["nodes"]] == [
        "retriever_bm25",
        "fusion",
        "context_builder",
        "generate",
    ]
    # The draft was not saved by running it
    assert (await owner_client.get(f"/pipelines/{created['id']}")).json()["version_count"] == 1


async def test_pipelines_are_scoped_to_the_organization(
    owner_client: AsyncClient, client: AsyncClient, super_admin: dict[str, Any]
) -> None:
    created = await _create(owner_client)
    login = await client.post(
        "/auth/login", json={"email": super_admin["email"], "password": super_admin["password"]}
    )
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Org-Id": "999",
    }
    resp = await client.get(f"/pipelines/{created['id']}", headers=headers)
    assert resp.status_code == 404
