"""Reciprocal Rank Fusion.

Retrievers score on incompatible scales - cosine similarity is bounded,
ts_rank_cd is not - so fusing on raw scores means one retriever quietly wins
every time. RRF only looks at rank position: a chunk found near the top by both
retrievers beats one found near the top by a single retriever.

    score(chunk) = sum over retrievers of 1 / (k + rank)

k (60 by convention) flattens the head of each list, so the difference between
rank 1 and rank 2 does not dominate agreement between retrievers.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from zk2.retrieval.base import RetrievedChunk

DEFAULT_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Iterable[Sequence[RetrievedChunk]],
    *,
    k: int = DEFAULT_K,
    limit: int | None = None,
) -> list[RetrievedChunk]:
    scores: dict[int, float] = {}
    best: dict[int, RetrievedChunk] = {}
    matched: dict[int, list[str]] = {}

    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            best.setdefault(chunk.chunk_id, chunk)
            retrievers = matched.setdefault(chunk.chunk_id, [])
            if chunk.retriever not in retrievers:
                retrievers.append(chunk.retriever)

    fused = [
        RetrievedChunk(
            chunk_id=chunk_id,
            source_id=best[chunk_id].source_id,
            source_name=best[chunk_id].source_name,
            ordinal=best[chunk_id].ordinal,
            text=best[chunk_id].text,
            score=score,
            retriever="rrf",
            matched_by=tuple(matched[chunk_id]),
        )
        for chunk_id, score in scores.items()
    ]
    # Ties broken by chunk id so the order is stable across runs
    fused.sort(key=lambda c: (-c.score, c.chunk_id))
    return fused[:limit] if limit is not None else fused
