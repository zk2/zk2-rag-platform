"""DTOs for the service switches and the host agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DesiredStateName = Literal["on", "off"]

# What the agent can see. `partial` is some containers up and some not - a
# crash, or a start that has not finished; `error` is docker compose failing.
ObservedStateName = Literal["running", "stopped", "partial", "starting", "stopping", "error"]


class ManagedServiceDto(BaseModel):
    name: str
    desired_state: DesiredStateName
    off_at: datetime | None
    observed_state: ObservedStateName | None
    observed_detail: str | None
    observed_at: datetime | None
    # False means nothing on the host is applying the switch: flipping it would
    # change a row in the database and nothing else
    agent_reporting: bool
    changed_at: datetime
    changed_by_email: str | None


class ServiceSwitch(BaseModel):
    on: bool
    # Only for switching on: Langfuse left running because somebody forgot it
    # is exactly the idle memory this switch exists to give back
    auto_off_hours: int | None = Field(None, ge=1, le=72)


class ServiceReport(BaseModel):
    name: str = Field(max_length=32)
    state: ObservedStateName
    detail: str | None = Field(None, max_length=2000)


class AgentReport(BaseModel):
    services: list[ServiceReport] = Field(max_length=16)


class DesiredState(BaseModel):
    name: str
    desired_state: DesiredStateName


class AgentInstructions(BaseModel):
    services: list[DesiredState]
