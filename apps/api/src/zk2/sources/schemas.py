"""Pydantic DTOs for sources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DirectoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: int | None = None


class UrlCreate(BaseModel):
    url: HttpUrl
    parent_id: int | None = None


class SitemapImport(BaseModel):
    base_url: HttpUrl
    parent_id: int | None = None
    # Each imported URL costs a fetch plus an embedding call, so the default is
    # deliberately small; /sources/sitemap/preview shows the real number first.
    limit: int = Field(100, ge=1, le=500)


class SitemapPreview(BaseModel):
    base_url: HttpUrl
    limit: int = Field(100, ge=1, le=500)


class SitemapPreviewDto(BaseModel):
    base_url: str
    total_found: int
    would_import: int
    urls: list[str]


class SourceDto(_Dto):
    id: int
    type: str
    name: str
    status: str
    error: str | None
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class SourceNodeDto(BaseModel):
    id: int
    type: str
    name: str
    status: str
    # Why indexing failed, so the tree can say it without a second request
    error: str | None = None
    children: list[SourceNodeDto] = []


SourceNodeDto.model_rebuild()


class ChunkDto(_Dto):
    id: int
    ordinal: int
    text: str
    tokens: int
