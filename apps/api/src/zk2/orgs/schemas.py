"""DTOs for organization settings."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EmbeddingSettingsDto(BaseModel):
    embedding_provider: str
    embedding_model: str
    dimensions: int
    # Sources whose vectors were built with a different model than the one configured
    stale_sources: int
    indexed_sources: int


class EmbeddingSettingsUpdate(BaseModel):
    embedding_provider: str = Field(..., min_length=1, max_length=32)
    embedding_model: str = Field(..., min_length=1, max_length=64)


class ReindexStartedDto(BaseModel):
    queued: int
    reason: str
