"""Ingest pipeline end-to-end plus dense retrieval over what it produced."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.retrieval.dense import dense_search
from zk2.sources.ingest import ingest_source
from zk2.sources.models import Source, SourceBM25, SourceChunk, SourceEmbedding, SourceStatus
from zk2.sources.service import create_directory, create_file_source

pytestmark = pytest.mark.integration

KEYWORDS = ("alpha", "beta", "gamma", "delta")


class FakeEmbeddings:
    """Deterministic keyword-count embeddings - no network, but still meaningful.

    A chunk about "alpha" lands close to the query "alpha" and far from "delta",
    which is what makes the retrieval assertions below worth anything.
    """

    name = "fake"
    model = "fake-embed-v1"
    dimensions = len(KEYWORDS)

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        counts = [float(lowered.count(word)) for word in KEYWORDS]
        norm = sum(c * c for c in counts) ** 0.5
        return [c / norm for c in counts] if norm else [0.0] * len(KEYWORDS)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _provider(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _provider)


async def _ingest_text(
    db: AsyncSession, *, org_id: int, name: str, body: str, parent_id: int | None = None
) -> Source:
    source = await create_file_source(
        db, None, org_id=org_id, parent_id=parent_id, filename=name, content=body.encode()
    )
    await db.commit()
    return source


async def test_ingest_produces_chunks_embeddings_and_bm25(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    org_id = org_owner["org_id"]
    source = await _ingest_text(
        db, org_id=org_id, name="alpha.txt", body="alpha " * 300 + "some trailing words"
    )

    await db.refresh(source)
    assert source.status == SourceStatus.READY.value, source.error
    assert source.error is None

    chunks = (
        (await db.execute(select(SourceChunk).where(SourceChunk.source_id == source.id)))
        .scalars()
        .all()
    )
    assert len(chunks) >= 1
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    embeddings = (await db.execute(select(func.count()).select_from(SourceEmbedding))).scalar_one()
    assert embeddings == len(chunks)

    bm25_rows = (await db.execute(select(func.count()).select_from(SourceBM25))).scalar_one()
    assert bm25_rows == len(chunks)

    assert source.meta is not None
    assert source.meta["chunks"] == len(chunks)
    assert source.lang == "en"
    assert source.lang_config == "english"


async def test_reingest_replaces_chunks_instead_of_duplicating(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    source = await _ingest_text(
        db, org_id=org_owner["org_id"], name="beta.txt", body="beta content here"
    )
    first = (await db.execute(select(func.count()).select_from(SourceChunk))).scalar_one()

    await ingest_source(db, source_id=source.id)
    await db.commit()

    second = (await db.execute(select(func.count()).select_from(SourceChunk))).scalar_one()
    assert second == first


async def test_empty_document_is_marked_failed(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    source = await _ingest_text(db, org_id=org_owner["org_id"], name="blank.txt", body="   \n  ")
    await db.refresh(source)
    assert source.status == SourceStatus.FAILED.value
    assert source.error


async def test_dense_search_ranks_the_matching_document_first(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    org_id = org_owner["org_id"]
    alpha = await _ingest_text(db, org_id=org_id, name="alpha.txt", body="alpha alpha alpha")
    delta = await _ingest_text(db, org_id=org_id, name="delta.txt", body="delta delta delta")

    query = await FakeEmbeddings().embed_query("alpha")
    hits = await dense_search(
        db,
        org_id=org_id,
        source_ids=[alpha.id, delta.id],
        query_embedding=query,
        embedding_model=FakeEmbeddings.model,
        k=5,
    )
    assert hits, "dense search returned nothing"
    assert hits[0].source_name == "alpha.txt"
    assert hits[0].score > 0.5


async def test_dense_search_expands_directories_to_descendants(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    org_id = org_owner["org_id"]
    folder = await create_directory(db, org_id=org_id, name="Folder", parent_id=None)
    await db.commit()
    await _ingest_text(db, org_id=org_id, name="gamma.txt", body="gamma gamma", parent_id=folder.id)

    query = await FakeEmbeddings().embed_query("gamma")
    hits = await dense_search(
        db,
        org_id=org_id,
        source_ids=[folder.id],  # a directory, not the file itself
        query_embedding=query,
        embedding_model=FakeEmbeddings.model,
        k=5,
    )
    assert [h.source_name for h in hits] == ["gamma.txt"]


async def test_dense_search_ignores_other_embedding_models(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """Chunks indexed with a different model must not leak into results."""
    org_id = org_owner["org_id"]
    source = await _ingest_text(db, org_id=org_id, name="alpha.txt", body="alpha alpha")

    hits = await dense_search(
        db,
        org_id=org_id,
        source_ids=[source.id],
        query_embedding=await FakeEmbeddings().embed_query("alpha"),
        embedding_model="some-other-model",
        k=5,
    )
    assert hits == []


async def test_dense_search_is_scoped_to_the_org(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    source = await _ingest_text(db, org_id=org_owner["org_id"], name="alpha.txt", body="alpha")
    hits = await dense_search(
        db,
        org_id=org_owner["org_id"] + 999,
        source_ids=[source.id],
        query_embedding=await FakeEmbeddings().embed_query("alpha"),
        embedding_model=FakeEmbeddings.model,
        k=5,
    )
    assert hits == []
