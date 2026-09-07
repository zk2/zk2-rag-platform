"""Experiments end to end: assignment, stickiness, analytics, promotion."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from tests.integration.test_rag_stream import FakeLLM
from zk2.ab.models import AbAssignment, AbExperiment
from zk2.ab.service import assign, running_experiment
from zk2.chat.rag import stream_rag
from zk2.pipelines.models import PipelineVersion

pytestmark = pytest.mark.integration


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


LEXICAL_DAG = {
    "nodes": [
        {"id": "bm25", "type": "retriever_bm25", "config": {"k": 5}},
        {"id": "fuse", "type": "fusion", "config": {"limit": 5}},
        {"id": "context", "type": "context_builder", "config": {"token_budget": 1000}},
        {"id": "answer", "type": "generate", "config": {}},
    ],
    "edges": [["bm25", "fuse"], ["fuse", "context"], ["context", "answer"]],
}


async def _bot_and_variant_version(owner_client: AsyncClient) -> tuple[int, int]:
    bot = (
        await owner_client.post("/bots", json={"name": "Helper", "llm_model": "gpt-4.1-mini"})
    ).json()
    pipeline = (await owner_client.post("/pipelines", json={"name": "Candidate"})).json()
    saved = await owner_client.put(
        f"/pipelines/{pipeline['id']}/dag", json={"dag": LEXICAL_DAG, "label": "lexical"}
    )
    return int(bot["id"]), int(saved.json()["id"])


async def _create_experiment(
    owner_client: AsyncClient, bot_id: int, version_id: int, split: tuple[int, int] = (50, 50)
) -> dict[str, Any]:
    resp = await owner_client.post(
        "/experiments",
        json={
            "bot_id": bot_id,
            "name": "Lexical vs hybrid",
            "variants": [
                {"name": "control", "traffic_percent": split[0], "is_control": True},
                {
                    "name": "lexical",
                    "pipeline_version_id": version_id,
                    "traffic_percent": split[1],
                },
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_creating_an_experiment_validates_the_split(
    owner_client: AsyncClient, fakes: FakeLLM
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    resp = await owner_client.post(
        "/experiments",
        json={
            "bot_id": bot_id,
            "name": "Broken",
            "variants": [
                {"name": "a", "traffic_percent": 70, "is_control": True},
                {"name": "b", "pipeline_version_id": version_id, "traffic_percent": 70},
            ],
        },
    )
    assert resp.status_code == 422
    assert "add up to 100" in resp.json()["error"]["message"]


async def test_start_and_stop(owner_client: AsyncClient, fakes: FakeLLM) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    assert experiment["status"] == "draft"

    started = await owner_client.post(f"/experiments/{experiment['id']}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert started.json()["started_at"]

    stopped = await owner_client.post(f"/experiments/{experiment['id']}/stop")
    assert stopped.json()["status"] == "stopped"


async def test_only_one_experiment_runs_per_bot(owner_client: AsyncClient, fakes: FakeLLM) -> None:
    """Two overlapping experiments would make both results meaningless."""
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    first = await _create_experiment(owner_client, bot_id, version_id)
    second = await _create_experiment(owner_client, bot_id, version_id)

    assert (await owner_client.post(f"/experiments/{first['id']}/start")).status_code == 200
    clash = await owner_client.post(f"/experiments/{second['id']}/start")
    assert clash.status_code == 422
    assert "already running" in clash.json()["error"]["message"]


async def test_assignment_is_sticky(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    await owner_client.post(f"/experiments/{experiment['id']}/start")

    row = await db.scalar(select(AbExperiment).where(AbExperiment.id == experiment["id"]))
    assert row is not None
    first = await assign(db, experiment=row, subject="user:42")
    second = await assign(db, experiment=row, subject="user:42")
    await db.commit()

    assert first is not None and second is not None
    assert first.variant_id == second.variant_id
    stored = (
        (await db.execute(select(AbAssignment).where(AbAssignment.experiment_id == row.id)))
        .scalars()
        .all()
    )
    assert len(stored) == 1


async def test_a_turn_runs_the_assigned_variant_and_tags_usage(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    # Everyone lands in the candidate so the assertion is not a coin flip
    experiment = await _create_experiment(owner_client, bot_id, version_id, split=(0, 100))
    await owner_client.post(f"/experiments/{experiment['id']}/start")
    await db.commit()

    events = []
    async for event in stream_rag(
        db,
        org_id=org_owner["org_id"],
        bot_id=bot_id,
        user=None,
        conversation_id=None,
        user_message="anything",
    ):
        events.append(event)
    await db.commit()

    done = next(e for e in events if e.kind == "done")
    assert done.payload["variant"] == "lexical"
    assert [n["type"] for n in done.payload["nodes"]] == [
        "retriever_bm25",
        "fusion",
        "context_builder",
        "generate",
    ]

    usage = (
        await db.execute(
            text(
                "SELECT metadata->>'experiment_id' AS e, metadata->>'variant_id' AS v FROM usage_events"
            )
        )
    ).all()
    assert usage[0].e == str(experiment["id"])
    assert usage[0].v


async def test_stats_group_by_variant(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id, split=(0, 100))
    await owner_client.post(f"/experiments/{experiment['id']}/start")
    await db.commit()

    async for _event in stream_rag(
        db,
        org_id=org_owner["org_id"],
        bot_id=bot_id,
        user=None,
        conversation_id=None,
        user_message="anything",
    ):
        pass
    await db.commit()

    stats = (await owner_client.get(f"/experiments/{experiment['id']}/stats")).json()
    by_name = {row["name"]: row for row in stats}
    assert by_name["lexical"]["calls"] == 1
    assert by_name["control"]["calls"] == 0
    assert by_name["control"]["is_control"] is True


async def test_promoting_a_variant_points_the_bot_at_it(
    owner_client: AsyncClient, db: AsyncSession, fakes: FakeLLM
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    await owner_client.post(f"/experiments/{experiment['id']}/start")

    variants = {v["name"]: v for v in experiment["variants"]}
    promoted = await owner_client.post(
        f"/experiments/{experiment['id']}/promote",
        json={"variant_id": variants["lexical"]["id"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "stopped"

    bot = (await owner_client.get(f"/bots/{bot_id}")).json()
    assert bot["pipeline_id"] is not None


async def test_promoting_the_control_is_refused(owner_client: AsyncClient, fakes: FakeLLM) -> None:
    """The control has no version of its own - there is nothing to promote."""
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    variants = {v["name"]: v for v in experiment["variants"]}

    resp = await owner_client.post(
        f"/experiments/{experiment['id']}/promote",
        json={"variant_id": variants["control"]["id"]},
    )
    assert resp.status_code == 422
    assert "nothing to promote" in resp.json()["error"]["message"]


async def test_no_experiment_means_no_variant_in_the_turn(
    owner_client: AsyncClient, db: AsyncSession, org_owner: dict[str, Any], fakes: FakeLLM
) -> None:
    bot = (
        await owner_client.post("/bots", json={"name": "Plain", "llm_model": "gpt-4.1-mini"})
    ).json()
    await db.commit()
    assert await running_experiment(db, bot_id=bot["id"]) is None

    events = []
    async for event in stream_rag(
        db,
        org_id=org_owner["org_id"],
        bot_id=bot["id"],
        user=None,
        conversation_id=None,
        user_message="hello",
    ):
        events.append(event)
    done = next(e for e in events if e.kind == "done")
    assert done.payload["variant"] is None


async def test_starting_pins_what_the_control_runs(
    owner_client: AsyncClient, db: AsyncSession
) -> None:
    """Control means "whatever the bot serves", and that moves under it.

    Promoting a version, or restoring one in the editor, changes what the
    control arm is measuring halfway through an experiment, and nothing says
    so. The graph is resolved once, when the experiment starts.
    """
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    pipeline_id = await db.scalar(
        select(PipelineVersion.pipeline_id).where(PipelineVersion.id == version_id)
    )
    await owner_client.patch(f"/bots/{bot_id}", json={"pipeline_id": pipeline_id})

    # A second version, so the bot's pipeline has somewhere to move to
    other = await owner_client.put(
        f"/pipelines/{pipeline_id}/dag",
        json={"dag": LEXICAL_DAG, "label": "second"},
    )
    other_id = int(other.json()["id"])

    experiment = await _create_experiment(owner_client, bot_id, version_id)
    started = await owner_client.post(f"/experiments/{experiment['id']}/start")
    assert started.status_code == 200, started.text

    control = next(v for v in started.json()["variants"] if v["is_control"])
    assert control["pipeline_version_id"] == other_id, "control kept the graph it started with"


async def test_an_experiment_needs_two_different_graphs(
    owner_client: AsyncClient, db: AsyncSession
) -> None:
    """Both arms on one graph produce two columns of the same numbers."""
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    pipeline_id = await db.scalar(
        select(PipelineVersion.pipeline_id).where(PipelineVersion.id == version_id)
    )
    await owner_client.patch(f"/bots/{bot_id}", json={"pipeline_id": pipeline_id})

    # The candidate is the very version the bot is already serving
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    started = await owner_client.post(f"/experiments/{experiment['id']}/start")

    assert started.status_code == 422, started.text
    assert "same graph as the control" in started.json()["error"]["message"]


async def test_a_finished_experiment_does_not_start_again(
    owner_client: AsyncClient,
) -> None:
    """Stopped is the end of a measurement, not a pause.

    Restarting would fold a second period of traffic into numbers already
    attached to the first, and the two are not comparable. The interface hid
    the Start button and said nothing, which reads as a broken screen.
    """
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    await owner_client.post(f"/experiments/{experiment['id']}/start")
    await owner_client.post(f"/experiments/{experiment['id']}/stop")

    again = await owner_client.post(f"/experiments/{experiment['id']}/start")
    assert again.status_code == 422, again.text
    assert "Create a new one" in again.json()["error"]["message"]


async def test_an_experiment_can_be_discarded(owner_client: AsyncClient) -> None:
    """The split cannot be edited after creation, so a wrong one needs a way out."""
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id, split=(90, 10))

    deleted = await owner_client.delete(f"/experiments/{experiment['id']}")
    assert deleted.status_code == 200, deleted.text
    listed = await owner_client.get(f"/experiments?bot_id={bot_id}")
    assert all(e["id"] != experiment["id"] for e in listed.json())


async def test_a_running_experiment_is_stopped_before_it_is_discarded(
    owner_client: AsyncClient,
) -> None:
    bot_id, version_id = await _bot_and_variant_version(owner_client)
    experiment = await _create_experiment(owner_client, bot_id, version_id)
    await owner_client.post(f"/experiments/{experiment['id']}/start")

    refused = await owner_client.delete(f"/experiments/{experiment['id']}")
    assert refused.status_code == 422
    assert "Stop the experiment" in refused.json()["error"]["message"]
