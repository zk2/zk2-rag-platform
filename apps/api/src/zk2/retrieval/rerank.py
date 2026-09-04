"""Cross-encoder reranking.

Retrieval returns candidates that look similar in vector space or share terms;
a cross-encoder reads the query and the passage together and scores how well
the passage actually answers it. That is a much better signal than either
retriever alone, at a cost that only makes sense on a short candidate list -
hence rerank after fusion, not instead of it.

The model runs locally (no API, no per-token cost) but pulls in torch, so
sentence-transformers is an optional extra:

    uv sync --extra rerank

When the extra is not installed, or the model cannot be loaded, reranking is
skipped and the fused order stands. A chat answer degrading in quality is
better than a chat endpoint that fails because a model file is missing.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Protocol

import structlog

from zk2.config import get_settings
from zk2.retrieval.base import RetrievedChunk

logger = structlog.get_logger()


class CrossEncoderLike(Protocol):
    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> Any: ...


_model: CrossEncoderLike | None = None
_load_failed = False
_lock = threading.Lock()


def _load_model() -> CrossEncoderLike | None:
    """Load the cross-encoder once per process. None means "run without it"."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        settings = get_settings().retrieval
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415  (optional extra)

            _model = CrossEncoder(settings.rerank_model)
            logger.info("rerank.model_loaded", model=settings.rerank_model)
        except Exception as exc:
            _load_failed = True
            logger.warning(
                "rerank.unavailable",
                model=settings.rerank_model,
                error=str(exc),
                hint="install the 'rerank' extra to enable cross-encoder reranking",
            )
        return _model


def reset_model_cache() -> None:
    """Test hook: forget the loaded model and any previous load failure."""
    global _model, _load_failed
    with _lock:
        _model = None
        _load_failed = False


def _score(model: CrossEncoderLike, query: str, chunks: list[RetrievedChunk]) -> list[float]:
    pairs = [(query, chunk.text) for chunk in chunks]
    return [float(score) for score in model.predict(pairs)]


async def rerank(
    query: str, chunks: list[RetrievedChunk], *, top_k: int | None = None
) -> list[RetrievedChunk]:
    """Reorder candidates by cross-encoder relevance, best first."""
    settings = get_settings().retrieval
    if not settings.rerank_enabled or len(chunks) < 2:
        return chunks[:top_k] if top_k else chunks

    model = await asyncio.to_thread(_load_model)
    if model is None:
        return chunks[:top_k] if top_k else chunks

    try:
        scores = await asyncio.to_thread(_score, model, query, chunks)
    except Exception:
        logger.exception("rerank.failed")
        return chunks[:top_k] if top_k else chunks

    ranked = [
        RetrievedChunk(
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            source_name=chunk.source_name,
            ordinal=chunk.ordinal,
            text=chunk.text,
            score=score,
            retriever="rerank",
            matched_by=chunk.matched_by or (chunk.retriever,),
        )
        for chunk, score in zip(chunks, scores, strict=True)
    ]
    ranked.sort(key=lambda c: (-c.score, c.chunk_id))
    return ranked[:top_k] if top_k else ranked
