"""DTOs for datasets, runs and comparisons."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zk2.evals.metrics import DETERMINISTIC_METRICS


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)


class DatasetDto(_Dto):
    id: int
    name: str
    description: str | None
    created_at: datetime
    item_count: int = 0


class ItemCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    expected_answer: str | None = Field(None, max_length=8000)
    expected_sources: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ItemDto(_Dto):
    id: int
    question: str
    expected_answer: str | None
    expected_sources: list[str]
    tags: list[str]


class ImportResultDto(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


class RunStart(BaseModel):
    bot_id: int
    label: str | None = Field(None, max_length=255)
    # A pinned graph, for comparing two pipeline versions on the same dataset
    pipeline_version_id: int | None = None
    # Judge metrics cost a model call per item, so they are opt-in
    metrics: list[str] = Field(default_factory=lambda: list(DETERMINISTIC_METRICS))
    judge_provider: str = "openai"
    judge_model: str = "gpt-4.1-mini"
    mark_baseline: bool = False


class RunDto(_Dto):
    id: int
    dataset_id: int
    bot_id: int | None
    pipeline_version_id: int | None
    label: str | None
    status: str
    error: str | None
    items_total: int
    items_done: int
    summary: dict[str, float]
    cost_usd: str | None
    duration_ms: int | None
    is_baseline: bool
    created_at: datetime


class ScoreDto(_Dto):
    id: int
    item_id: int
    answer: str | None
    error: str | None
    metrics: dict[str, float]
    retrieved: list[dict[str, Any]]
    latency_ms: int | None


class ComparisonDto(BaseModel):
    baseline_run_id: int
    candidate_run_id: int
    metrics: dict[str, dict[str, float]]
    regressions: list[str]
    threshold: float
