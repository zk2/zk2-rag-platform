"""DTOs for agent tools and MCP servers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ToolDto(BaseModel):
    name: str
    description: str
    kind: str
    args_schema: dict[str, Any]


class McpServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    url: HttpUrl
    enabled: bool = True


class McpServerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    url: HttpUrl | None = None
    enabled: bool | None = None


class McpServerDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    enabled: bool
    status: str
    last_error: str | None
    tool_count: int | None
    last_checked_at: datetime | None
    created_at: datetime
