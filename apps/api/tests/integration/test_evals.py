"""Datasets, runs and comparisons, end to end against the real pipeline."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from tests.integration.test_rag_stream import FakeLLM
from zk2.core.db import db_session
from zk2.evals.models import EvalRun
from zk2.evals.runner import RunSettings, compare, execute_run, regressions
from zk2.evals.schemas import RunStart
from zk2.evals.service import start_run

pytestmark = pytest.mark.integration

CSV_CONTENT = (
    "question,expected_answer,expected_sources,tags\n"
    "How much alpha?,Three alphas,alpha.txt,smoke\n"
    "What about beta?,No beta here,,edge\n"
)


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
    monkeypatch.setattr("zk2.evals.runner.get_llm_provider", _get_llm)
    return llm


async def _bot_with_source(owner_client: AsyncClient, db: AsyncSession) -> int:
    upload = await owner_client.post(
        "/sources/file", files={"file": ("alpha.txt", b"alpha alpha alpha", "text/plain")}
    )
    from zk2.sources.ingest import ingest_source

    await ingest_source(db, source_id=upload.json()["id"])
    await db.commit()
    bot = await owner_client.post(
        "/bots",
        json={"name": "Helper", "llm_model": "gpt-4.1-mini", "source_ids": [upload.json()["id"]]},
    )
    return int(bot.json()["id"])


async def _dataset_with_items(owner_client: AsyncClient) -> int:
    created = await owner_client.post("/evals/datasets", json={"name": "Golden set"})
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    imported = await owner_client.post(
        f"/evals/datasets/{dataset_id}/import",
        files={"file": ("items.csv", CSV_CONTENT.encode(), "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 2
    return int(dataset_id)


async def test_metrics_endpoint_flags_which_cost_money(owner_client: AsyncClient) -> None:
    body = (await owner_client.get("/evals/metrics")).json()
    by_name = {m["name"]: m for m in body}
    assert by_name["retrieval_recall"]["needs_judge"] is False
    assert by_name["faithfulness"]["needs_judge"] is True


async def test_csv_import_parses_lists(owner_client: AsyncClient) -> None:
    dataset_id = await _dataset_with_items(owner_client)
    items = (await owner_client.get(f"/evals/datasets/{dataset_id}/items")).json()
    assert [i["question"] for i in items] == ["How much alpha?", "What about beta?"]
    assert items[0]["expected_sources"] == ["alpha.txt"]
    assert items[0]["tags"] == ["smoke"]
    assert items[1]["expected_sources"] == []


async def test_json_import_is_accepted_too(owner_client: AsyncClient) -> None:
    created = await owner_client.post("/evals/datasets", json={"name": "From JSON"})
    dataset_id = created.json()["id"]
    payload = b'[{"question": "Q1", "expected_sources": ["a.md"]}, {"noquestion": 1}]'
    result = await owner_client.post(
        f"/evals/datasets/{dataset_id}/import",
        files={"file": ("items.json", payload, "application/json")},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["imported"] == 1
    assert body["skipped"] == 1
    assert "no question" in body["errors"][0]


async def test_a_run_scores_every_item(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id = await _bot_with_source(owner_client, db)
    dataset_id = await _dataset_with_items(owner_client)

    started = await owner_client.post(
        f"/evals/datasets/{dataset_id}/runs",
        json={"bot_id": bot_id, "metrics": ["retrieval_recall", "answered"], "label": "first"},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]

    # The API hands the run to the worker; run it inline for the test
    await execute_run(
        db, run_id=run_id, settings=RunSettings(metrics=["retrieval_recall", "answered"])
    )
    await db.commit()

    run = (await owner_client.get(f"/evals/runs/{run_id}")).json()
    assert run["status"] == "done"
    assert run["items_total"] == 2
    assert run["items_done"] == 2
    assert set(run["summary"]) == {"retrieval_recall", "answered"}

    scores = (await owner_client.get(f"/evals/runs/{run_id}/scores")).json()
    assert len(scores) == 2
    assert all(s["answer"] for s in scores)


async def test_expected_sources_drive_retrieval_recall(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    """The first item expects alpha.txt and gets it; the second expects nothing."""
    bot_id = await _bot_with_source(owner_client, db)
    dataset_id = await _dataset_with_items(owner_client)
    started = await owner_client.post(
        f"/evals/datasets/{dataset_id}/runs",
        json={"bot_id": bot_id, "metrics": ["retrieval_recall"]},
    )
    await execute_run(
        db, run_id=started.json()["id"], settings=RunSettings(metrics=["retrieval_recall"])
    )
    await db.commit()

    run = (await owner_client.get(f"/evals/runs/{started.json()['id']}")).json()
    assert run["summary"]["retrieval_recall"] == 1.0


async def test_unknown_metric_is_rejected(
    owner_client: AsyncClient, db: AsyncSession, fakes: FakeLLM
) -> None:
    bot_id = await _bot_with_source(owner_client, db)
    dataset_id = await _dataset_with_items(owner_client)
    resp = await owner_client.post(
        f"/evals/datasets/{dataset_id}/runs",
        json={"bot_id": bot_id, "metrics": ["vibes"]},
    )
    assert resp.status_code == 422
    assert "Unknown metrics" in resp.json()["error"]["message"]


async def test_running_an_empty_dataset_is_refused(
    owner_client: AsyncClient, db: AsyncSession, fakes: FakeLLM
) -> None:
    bot_id = await _bot_with_source(owner_client, db)
    empty = await owner_client.post("/evals/datasets", json={"name": "Empty"})
    resp = await owner_client.post(
        f"/evals/datasets/{empty.json()['id']}/runs",
        json={"bot_id": bot_id, "metrics": ["answered"]},
    )
    assert resp.status_code == 422
    assert "no items" in resp.json()["error"]["message"]


async def test_comparison_names_the_regressions(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id = await _bot_with_source(owner_client, db)
    dataset_id = await _dataset_with_items(owner_client)

    baseline = await start_run(
        db,
        None,
        org_id=org_owner["org_id"],
        user=await _user(db, org_owner),
        dataset_id=dataset_id,
        payload=RunStart(bot_id=bot_id, metrics=["answered"], mark_baseline=True),
    )
    candidate = await start_run(
        db,
        None,
        org_id=org_owner["org_id"],
        user=await _user(db, org_owner),
        dataset_id=dataset_id,
        payload=RunStart(bot_id=bot_id, metrics=["answered"]),
    )
    # Simulate the candidate doing worse
    candidate.summary = {"answered": 0.5}
    await db.commit()

    body = (await owner_client.get(f"/evals/runs/{baseline.id}/compare/{candidate.id}")).json()
    assert body["metrics"]["answered"]["baseline"] == 1.0
    assert body["metrics"]["answered"]["delta"] == -0.5
    assert body["regressions"] == ["answered"]


async def test_runs_over_different_datasets_are_not_comparable(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id = await _bot_with_source(owner_client, db)
    first = await _dataset_with_items(owner_client)
    second = await _dataset_with_items(owner_client)
    user = await _user(db, org_owner)
    run_a = await start_run(
        db,
        None,
        org_id=org_owner["org_id"],
        user=user,
        dataset_id=first,
        payload=RunStart(bot_id=bot_id, metrics=["answered"]),
    )
    run_b = await start_run(
        db,
        None,
        org_id=org_owner["org_id"],
        user=user,
        dataset_id=second,
        payload=RunStart(bot_id=bot_id, metrics=["answered"]),
    )
    await db.commit()
    resp = await owner_client.get(f"/evals/runs/{run_a.id}/compare/{run_b.id}")
    assert resp.status_code == 422
    assert "same dataset" in resp.json()["error"]["message"]


def test_regression_detection_respects_the_threshold() -> None:
    comparison = {
        "faithfulness": {"baseline": 0.9, "candidate": 0.87, "delta": -0.03},
        "correctness": {"baseline": 0.8, "candidate": 0.6, "delta": -0.2},
        "answered": {"baseline": 0.9, "candidate": 1.0, "delta": 0.1},
    }
    assert regressions(comparison, threshold=0.05) == ["correctness"]
    assert set(regressions(comparison, threshold=0.01)) == {"faithfulness", "correctness"}


def test_compare_handles_a_metric_present_in_only_one_run() -> None:
    baseline = EvalRun(summary={"answered": 1.0})
    candidate = EvalRun(summary={"answered": 1.0, "faithfulness": 0.7})
    result = compare(baseline, candidate)
    assert result["faithfulness"] == {"baseline": 0.0, "candidate": 0.7, "delta": 0.7}


async def _user(db: AsyncSession, org_owner: dict[str, Any]) -> Any:
    from sqlalchemy import select

    from zk2.auth.models import User

    return await db.scalar(select(User).where(User.id == org_owner["user_id"]))


async def test_a_run_can_be_pinned_to_a_pipeline_version(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """Comparing two graphs means running both against the same questions.

    Without a pinned version the only way to measure a change was to make it
    the bot's current pipeline first, which changes what everyone else is
    talking to while the measurement runs.
    """
    dataset_id = await _dataset_with_items(owner_client)
    bot_id = await _bot_with_source(owner_client, db)

    pipeline = (await owner_client.post("/pipelines", json={"name": "Lexical only"})).json()
    lean = {
        "nodes": [
            {"id": "bm25", "type": "retriever_bm25", "config": {"k": 5}},
            {"id": "context", "type": "context_builder", "config": {}},
            {"id": "answer", "type": "generate", "config": {}},
        ],
        "edges": [["bm25", "context"], ["context", "answer"]],
    }
    version = (
        await owner_client.put(f"/pipelines/{pipeline['id']}/dag", json={"dag": lean})
    ).json()

    started = await owner_client.post(
        f"/evals/datasets/{dataset_id}/runs",
        json={
            "bot_id": bot_id,
            "pipeline_version_id": version["id"],
            "metrics": ["citation_rate"],
        },
    )
    assert started.status_code == 201, started.text
    assert started.json()["pipeline_version_id"] == version["id"]

    run = await db.get(EvalRun, started.json()["id"])
    assert run is not None
    assert run.pipeline_version_id == version["id"]


async def test_progress_is_visible_while_the_run_is_still_going(
    owner_client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    fakes: FakeLLM,
) -> None:
    """Nothing else can see an open transaction.

    Progress used to be flushed and not committed, so the API answering the
    poll saw "pending" and zero items from start to finish and then a finished
    run - which looks exactly like a worker that never picked the job up.

    The observation is taken from its own session, because that is the only
    kind that proves the point.
    """
    from zk2.evals import runner as runner_module

    dataset_id = await _dataset_with_items(owner_client)
    bot_id = await _bot_with_source(owner_client, db)
    started = await owner_client.post(
        f"/evals/datasets/{dataset_id}/runs",
        json={"bot_id": bot_id, "metrics": ["citation_rate"]},
    )
    run_id = int(started.json()["id"])
    await db.commit()

    seen: list[tuple[str, int]] = []
    real_execute = runner_module.execute

    def spy(*args: Any, **kwargs: Any) -> Any:
        async def _wrapped() -> Any:
            async with db_session() as watcher:
                row = await watcher.get(EvalRun, run_id)
                assert row is not None
                seen.append((row.status, row.items_done))
            async for event in real_execute(*args, **kwargs):
                yield event

        return _wrapped()

    monkeypatch.setattr(runner_module, "execute", spy)
    await execute_run(db, run_id=run_id, settings=RunSettings(metrics=["citation_rate"]))

    assert seen, "the run never reached an item"
    assert seen[0] == ("running", 0), seen
    assert seen[-1][1] >= 1, "later items saw no progress from earlier ones"
