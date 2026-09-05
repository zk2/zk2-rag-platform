"""DTOs for experiments."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VariantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    # None means "whatever the bot runs today" - the control needs no version
    pipeline_version_id: int | None = None
    traffic_percent: int = Field(..., ge=0, le=100)
    is_control: bool = False


class ExperimentCreate(BaseModel):
    bot_id: int
    name: str = Field(..., min_length=1, max_length=255)
    variants: list[VariantCreate] = Field(..., min_length=2, max_length=6)


class VariantDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    pipeline_version_id: int | None
    traffic_percent: int
    is_control: bool


class ExperimentDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bot_id: int
    name: str
    status: str
    started_at: datetime | None
    stopped_at: datetime | None
    created_at: datetime
    variants: list[VariantDto] = []


class VariantStatsDto(BaseModel):
    variant_id: int
    name: str
    is_control: bool
    traffic_percent: int
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: str


class PromoteRequest(BaseModel):
    variant_id: int
