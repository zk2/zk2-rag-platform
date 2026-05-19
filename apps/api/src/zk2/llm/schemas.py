"""Pydantic DTOs for LLM provider settings."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProviderUpsert(BaseModel):
    api_key: str | None = Field(None, min_length=10, max_length=2048)
    custom_base_url: str | None = Field(None, max_length=512)


class ProviderDto(_Dto):
    id: int
    provider: str
    has_key: bool
    custom_base_url: str | None
    updated_at: datetime
