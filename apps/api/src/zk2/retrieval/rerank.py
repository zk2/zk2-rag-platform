"""Cross-encoder reranking.

Retrieval returns candidates that look similar in vector space or share terms;
a cross-encoder reads the query and the passage together and scores how well
the passage actually answers it. That is a much better signal than either
retriever alone, at a cost that only makes sense on a short candidate list -
hence rerank after fusion, not instead of it.

The model runs locally - no API, no per-token cost - but it reads query and
passage together, so it costs one forward pass per candidate and that cost is
paid on every turn by the user waiting for an answer. On CPU that is the whole
design constraint: measured on twelve cores over thirty candidates, the default
multilingual MiniLM takes 3.5s, bge-reranker-base 18s and bge-reranker-v2-m3
30s against a turn that otherwise runs in two to four seconds. Hence a small
model by default, a candidate list capped well below what fusion produces, and
the bigger models left as a setting for deployments with a GPU.

torch comes in through an optional extra:

    uv sync --extra rerank

When the extra is not installed, or the model cannot be loaded, reranking is
skipped and the fused order stands. A chat answer degrading in quality is
better than a chat endpoint that fails because a model file is missing.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
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

            _model = CrossEncoder(settings.rerank_model, max_length=settings.rerank_max_tokens)
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


async def warm_up() -> None:
    """Load the model outside a user's turn.

    Loading takes seconds even from a warm cache, and whoever asked the first
    question should not be the one paying for it.
    """
    if not get_settings().retrieval.rerank_enabled:
        return
    await asyncio.to_thread(_load_model)


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

    # Scoring is linear in candidates and the user is waiting, so only the head
    # of the fused list is scored. What is dropped here kept its fused rank and
    # would have had to beat fifteen better-ranked passages to be used at all.
    scored, rest = chunks[: settings.rerank_candidates], chunks[settings.rerank_candidates :]

    try:
        scores = await asyncio.to_thread(_score, model, query, scored)
    except Exception:
        logger.exception("rerank.failed")
        return chunks[:top_k] if top_k else chunks

    ranked = [
        replace(
            chunk,
            score=score,
            retriever="rerank",
            matched_by=chunk.matched_by or (chunk.retriever,),
        )
        for chunk, score in zip(scored, scores, strict=True)
    ]
    ranked.sort(key=lambda c: (-c.score, c.chunk_id))
    ranked.extend(rest)
    return ranked[:top_k] if top_k else ranked
