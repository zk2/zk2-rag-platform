"""Seed the super-admin and a workspace to sign into. Idempotent.

The super-admin flag alone does not make the product usable: every page under
the app zone resolves an organization from membership, so an account with no
organization can only see the admin queue. The seed therefore also creates one
organization and puts the super-admin in it as owner - which is what the first
run of `make seed` is expected to produce.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from slugify import slugify
from sqlalchemy import select

from zk2.auth.models import Membership, Organization, User
from zk2.config import get_settings
from zk2.core.db import db_session
from zk2.core.logging import configure_logging
from zk2.core.security import hash_password
from zk2.orgs.models import OrgSettings

logger = structlog.get_logger()

DEFAULT_ORG_NAME = "Demo workspace"


async def _ensure_super_admin(db, email: str, password: str) -> User:  # type: ignore[no-untyped-def]
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if user is not None:
        if not user.is_super_admin:
            user.is_super_admin = True
            logger.info("seed.super_admin_promoted", email=email)
        else:
            logger.info("seed.super_admin_exists", email=email)
        return user

    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        is_super_admin=True,
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    logger.info("seed.super_admin_created", email=email)
    return user


async def _ensure_workspace(db, user: User) -> Organization:  # type: ignore[no-untyped-def]
    membership = await db.scalar(select(Membership).where(Membership.user_id == user.id))
    if membership is not None:
        org = await db.scalar(select(Organization).where(Organization.id == membership.org_id))
        logger.info("seed.workspace_exists", org=org.slug if org else membership.org_id)
        return org  # type: ignore[return-value]

    slug = slugify(DEFAULT_ORG_NAME)
    org = await db.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        org = Organization(slug=slug, name=DEFAULT_ORG_NAME)
        db.add(org)
        await db.flush()
        db.add(OrgSettings(org_id=org.id))
        logger.info("seed.workspace_created", org=slug)

    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    await db.flush()
    logger.info("seed.membership_created", org=org.slug, email=user.email)
    return org


async def main() -> None:
    configure_logging()
    settings = get_settings()

    email = settings.super_admin.email
    password = settings.super_admin.password
    if not email or not password:
        logger.error(
            "seed.missing_env",
            message="Set SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD in env",
        )
        return

    async with db_session() as db:
        user = await _ensure_super_admin(db, email, password.get_secret_value())
        await _ensure_workspace(db, user)


if __name__ == "__main__":
    asyncio.run(main())
