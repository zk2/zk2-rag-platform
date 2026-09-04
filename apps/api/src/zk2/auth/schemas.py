"""Pydantic DTOs for auth endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _Dto(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Requests ───────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=255)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(..., min_length=10)


class AccessRequestCreate(BaseModel):
    email: EmailStr
    message: str | None = Field(None, max_length=2000)


class InviteAcceptRequest(BaseModel):
    token: str = Field(..., min_length=10)
    password: str = Field(..., min_length=12, max_length=255)
    full_name: str | None = Field(None, max_length=255)


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = Field("owner")
    org_id: int | None = None
    create_org_name: str | None = Field(
        None,
        max_length=255,
        description="If org_id is null, a new organization is created with this name",
    )


class AccessRequestDecision(BaseModel):
    approve: bool
    reason: str | None = Field(None, max_length=2000)
    create_org_name: str | None = Field(None, max_length=255)


# ─── Responses ──────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105  (OAuth token type, not a secret)
    expires_in: int


class UserDto(_Dto):
    id: int
    email: EmailStr
    full_name: str | None
    is_super_admin: bool
    is_active: bool
    created_at: datetime


class MembershipDto(_Dto):
    org_id: int
    org_slug: str
    org_name: str
    role: str


class MeResponse(BaseModel):
    user: UserDto
    memberships: list[MembershipDto]


class AccessRequestDto(_Dto):
    id: int
    email: EmailStr
    message: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None


class InviteDto(_Dto):
    id: int
    email: EmailStr
    role: str
    org_id: int | None
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


class GenericMessage(BaseModel):
    message: str
