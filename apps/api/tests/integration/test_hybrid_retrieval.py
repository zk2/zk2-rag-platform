"""BM25 over per-source language, and the hybrid path end to end."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_ingest_and_retrieval import FakeEmbeddings
from zk2.retrieval.bm25 import bm25_search
from zk2.retrieval.dense import dense_search
from zk2.retrieval.fusion import reciprocal_rank_fusion
from zk2.sources.models import Source
from zk2.sources.service import create_file_source

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _provider(*_args: object, **_kwargs: object) -> FakeEmbeddings:
        return FakeEmbeddings()

    monkeypatch.setattr("zk2.sources.ingest.get_embedding_provider", _provider)


async def _ingest(db: AsyncSession, *, org_id: int, name: str, body: str) -> Source:
    source = await create_file_source(
        db, None, org_id=org_id, parent_id=None, filename=name, content=body.encode()
    )
    await db.commit()
    await db.refresh(source)
    return source


async def test_bm25_finds_an_exact_term(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    """The case dense retrieval is worst at: a rare literal token."""
    org_id = org_owner["org_id"]
    source = await _ingest(
        db,
        org_id=org_id,
        name="errors.txt",
        body="The service returns ERR_QUOTA_EXHAUSTED when the monthly limit is reached.",
    )
    hits = await bm25_search(
        db, org_id=org_id, source_ids=[source.id], query="ERR_QUOTA_EXHAUSTED", k=5
    )
    assert [h.source_name for h in hits] == ["errors.txt"]
    assert hits[0].score > 0
    assert hits[0].retriever == "bm25"


async def test_bm25_uses_the_documents_language(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """Russian stemming: a query in the nominative matches an inflected document."""
    org_id = org_owner["org_id"]
    source = await _ingest(
        db,
        org_id=org_id,
        name="dogovor.txt",
        body="Договоры аренды нежилых помещений заключаются в письменной форме.",
    )
    assert source.lang == "ru"
    assert source.lang_config == "russian"

    hits = await bm25_search(db, org_id=org_id, source_ids=[source.id], query="договор", k=5)
    assert [h.source_name for h in hits] == ["dogovor.txt"]


async def test_ukrainian_falls_back_to_simple_and_still_matches(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    org_id = org_owner["org_id"]
    source = await _ingest(
        db,
        org_id=org_id,
        name="ua.txt",
        body="Обладнання встановлюється відповідно до інструкції користувача.",
    )
    assert source.lang == "uk"
    assert source.lang_config == "simple"

    hits = await bm25_search(db, org_id=org_id, source_ids=[source.id], query="обладнання", k=5)
    assert [h.source_name for h in hits] == ["ua.txt"]


async def test_bm25_returns_nothing_for_an_unrelated_query(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    source = await _ingest(db, org_id=org_owner["org_id"], name="a.txt", body="alpha beta gamma")
    hits = await bm25_search(
        db, org_id=org_owner["org_id"], source_ids=[source.id], query="helicopter", k=5
    )
    assert hits == []


async def test_bm25_is_scoped_to_the_org(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    source = await _ingest(db, org_id=org_owner["org_id"], name="a.txt", body="alpha beta gamma")
    hits = await bm25_search(
        db, org_id=org_owner["org_id"] + 999, source_ids=[source.id], query="alpha", k=5
    )
    assert hits == []


async def test_empty_query_is_not_sent_to_postgres(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    source = await _ingest(db, org_id=org_owner["org_id"], name="a.txt", body="alpha")
    assert (
        await bm25_search(db, org_id=org_owner["org_id"], source_ids=[source.id], query="   ", k=5)
        == []
    )


async def test_hybrid_surfaces_what_each_retriever_alone_would_miss(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    """Dense knows the keyword documents; BM25 knows the literal token."""
    org_id = org_owner["org_id"]
    semantic = await _ingest(db, org_id=org_id, name="alpha.txt", body="alpha alpha alpha")
    lexical = await _ingest(
        db, org_id=org_id, name="codes.txt", body="Error ERR_ALPHA_MISSING is raised at startup."
    )
    source_ids = [semantic.id, lexical.id]

    dense_hits = await dense_search(
        db,
        org_id=org_id,
        source_ids=source_ids,
        query_embedding=await FakeEmbeddings().embed_query("alpha"),
        embedding_model=FakeEmbeddings.model,
        k=20,
    )
    lexical_hits = await bm25_search(
        db, org_id=org_id, source_ids=source_ids, query="ERR_ALPHA_MISSING", k=20
    )
    assert [h.source_name for h in lexical_hits] == ["codes.txt"]

    fused = reciprocal_rank_fusion([dense_hits, lexical_hits], limit=5)
    names = {h.source_name for h in fused}
    assert names == {"alpha.txt", "codes.txt"}
    assert all(h.retriever == "rrf" for h in fused)


async def test_reindex_updates_the_language(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    """A re-ingested document is re-analysed, not left on the old configuration."""
    org_id = org_owner["org_id"]
    source = await _ingest(db, org_id=org_id, name="doc.txt", body="plain english content here")
    assert source.lang == "en"

    from zk2.sources.ingest import ingest_source

    source.meta = {**(source.meta or {}), "storage_key": (source.meta or {})["storage_key"]}
    stored = await db.scalar(select(Source).where(Source.id == source.id))
    assert stored is not None
    await ingest_source(db, source_id=source.id)
    await db.commit()
    await db.refresh(stored)
    assert stored.lang == "en"
    assert stored.lang_config == "english"
