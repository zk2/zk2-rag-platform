"""DTOs for the pipeline editor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zk2.pipelines.dag import DagSpec


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class NodeTypeDto(BaseModel):
    type: str
    title: str
    description: str
    # JSON Schema of the node's config model - the inspector form is built from it
    config_schema: dict[str, Any]


class PipelineCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    # Omitted: the pipeline starts as a copy of the built-in default
    dag: DagSpec | None = None


class PipelinePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)


class PipelineDto(_Dto):
    id: int
    name: str
    description: str | None
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime


class PipelineDetailDto(PipelineDto):
    dag: DagSpec
    version_count: int
    used_by_bots: list[int]


class DagSave(BaseModel):
    dag: DagSpec
    label: str | None = Field(None, max_length=255)


class PipelineVersionDto(_Dto):
    id: int
    pipeline_id: int
    label: str | None
    created_by_user_id: int | None
    created_at: datetime
    is_current: bool = False


class PipelineVersionDetailDto(PipelineVersionDto):
    dag: DagSpec


class TestRunRequest(BaseModel):
    bot_id: int
    question: str = Field(..., min_length=1, max_length=4000)
    # Test the working copy without saving it first
    dag: DagSpec | None = None


class NodeTimingDto(BaseModel):
    node_id: str
    type: str
    duration_ms: int


class TestRunDto(BaseModel):
    status: str
    question: str
    answer: str | None
    error: str | None
    duration_ms: int
    nodes: list[NodeTimingDto]
    sources: list[dict[str, Any]]
    citations: list[int]
    tokens_in: int
    tokens_out: int
    cost_usd: str | None
    trace_url: str | None
