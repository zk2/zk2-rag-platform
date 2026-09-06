"""DTOs for organization settings."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from zk2.sources.chunking import (
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    ChunkStrategy,
)


class EmbeddingSettingsDto(BaseModel):
    """Everything that decides how the corpus is indexed, plus what is stale.

    Chunking sits here rather than on a bot because sources belong to the
    organization and are shared between bots: one corpus, one way of cutting it.
    """

    embedding_provider: str
    embedding_model: str
    dimensions: int
    chunk_size: int
    chunk_overlap: int
    chunk_strategy: ChunkStrategy
    # Sources indexed with different settings than the ones configured now -
    # a different embedding model, different chunking, or an older chunker
    stale_sources: int
    indexed_sources: int


class EmbeddingSettingsUpdate(BaseModel):
    embedding_provider: str = Field(..., min_length=1, max_length=32)
    embedding_model: str = Field(..., min_length=1, max_length=64)


class ChunkingSettingsUpdate(BaseModel):
    chunk_size: int = Field(..., ge=MIN_CHUNK_TOKENS, le=MAX_CHUNK_TOKENS)
    chunk_overlap: int = Field(..., ge=0, le=MAX_CHUNK_TOKENS)
    chunk_strategy: ChunkStrategy

    @model_validator(mode="after")
    def _overlap_fits(self) -> ChunkingSettingsUpdate:
        if self.chunk_overlap >= self.chunk_size:
            msg = "chunk_overlap must be smaller than chunk_size"
            raise ValueError(msg)
        return self


class ReindexStartedDto(BaseModel):
    queued: int
    reason: str
