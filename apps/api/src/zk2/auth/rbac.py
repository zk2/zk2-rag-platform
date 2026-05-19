"""Role-based access control primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import Membership, User
from zk2.core.deps import current_user_dep
from zk2.core.errors import Forbidden, NotFound


ROLES_RANK = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}


@dataclass(slots=True)
class OrgContext:
    user: User
    org_id: int
    role: str

    def has_at_least(self, role: str) -> bool:
        return ROLES_RANK.get(self.role, 0) >= ROLES_RANK.get(role, 99)


def require_super_admin() -> Callable[[User], User]:
    async def _dep(user: Annotated[User, Depends(current_user_dep)]) -> User:
        if not user.is_super_admin:
            raise Forbidden("Super-admin only")
        return user

    return _dep


def require_org(min_role: str = "viewer") -> Callable[..., OrgContext]:
    """Resolve current org via `X-Org-Id` header and verify membership/role."""
    from zk2.core.deps import get_db_dep  # local import to avoid cycle

    async def _dep(
        user: Annotated[User, Depends(current_user_dep)],
        db: Annotated[AsyncSession, Depends(get_db_dep)],
        x_org_id: Annotated[int | None, Header(alias="X-Org-Id")] = None,
    ) -> OrgContext:
        if x_org_id is None:
            raise Forbidden("X-Org-Id header required")

        if user.is_super_admin:
            return OrgContext(user=user, org_id=x_org_id, role="owner")

        result = await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == x_org_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise NotFound("Organization not found or no access")

        ctx = OrgContext(user=user, org_id=x_org_id, role=membership.role)
        if not ctx.has_at_least(min_role):
            raise Forbidden(f"Requires role >= {min_role}")
        return ctx

    return _dep
