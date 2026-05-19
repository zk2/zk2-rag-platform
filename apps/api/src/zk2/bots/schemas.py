"""Pydantic DTOs for bots."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    system_prompt: str | None = Field(None, max_length=20_000)
    llm_provider: str = Field("openai")
    llm_model: str = Field("gpt-4o-mini")
    temperature: float = Field(0, ge=0, le=2)
    num_k: int = Field(5, ge=1, le=20)
    source_ids: list[int] = []


class BotPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    system_prompt: str | None = Field(None, max_length=20_000)
    llm_provider: str | None = None
    llm_model: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    num_k: int | None = Field(None, ge=1, le=20)
    source_ids: list[int] | None = None


class BotDto(_Dto):
    id: int
    name: str
    system_prompt: str | None
    llm_provider: str
    llm_model: str
    temperature: float
    num_k: int
    source_ids: list[int]
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime
