"""Seed super-admin from env. Idempotent."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from zk2.auth.models import User
from zk2.config import get_settings
from zk2.core.db import db_session
from zk2.core.logging import configure_logging
from zk2.core.security import hash_password

logger = structlog.get_logger()


async def main() -> None:
    configure_logging()
    settings = get_settings()

    email = settings.super_admin.email
    pwd = settings.super_admin.password
    if not email or not pwd:
        logger.error(
            "seed.missing_env",
            message="Set SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD in env",
        )
        return

    async with db_session() as db:
        existing = await db.scalar(select(User).where(User.email == email.lower()))
        if existing is not None:
            if not existing.is_super_admin:
                existing.is_super_admin = True
                logger.info("seed.super_admin_promoted", email=email)
            else:
                logger.info("seed.super_admin_already_exists", email=email)
            return
        db.add(
            User(
                email=email.lower(),
                password_hash=hash_password(pwd.get_secret_value()),
                is_super_admin=True,
                is_active=True,
                email_verified_at=datetime.now(UTC),
            )
        )
        logger.info("seed.super_admin_created", email=email)


if __name__ == "__main__":
    asyncio.run(main())
