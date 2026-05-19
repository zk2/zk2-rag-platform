"""Auth business logic (login, sessions, magic links)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import MagicLink, Session, User
from zk2.config import get_settings
from zk2.core.audit import write_audit
from zk2.core.email import send_templated
from zk2.core.errors import Unauthorized
from zk2.core.rate_limit import hit as rate_limit_hit
from zk2.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_token,
    needs_rehash,
    verify_password,
    hash_password,
)

logger = structlog.get_logger()


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ─── Login / refresh / logout ───────────────────────────────


async def authenticate_user(
    db: AsyncSession,
    redis: Redis,
    *,
    email: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, str, str, int]:
    settings = get_settings()

    await rate_limit_hit(
        redis,
        key=f"rl:login:ip:{ip or 'unknown'}",
        limit=settings.auth.rate_limit_login_per_min,
        window_seconds=60,
        error_message="Too many login attempts from this IP",
    )

    user = await db.scalar(select(User).where(User.email == email))
    if user is None or user.password_hash is None or not user.is_active:
        await write_audit(
            db,
            action="auth.login.failed",
            payload={"email": email, "reason": "user_not_found_or_inactive"},
            ip=ip,
            user_agent=user_agent,
        )
        raise Unauthorized("Invalid credentials")

    if not verify_password(password, user.password_hash):
        await write_audit(
            db,
            action="auth.login.failed",
            actor_user_id=user.id,
            payload={"email": email, "reason": "bad_password"},
            ip=ip,
            user_agent=user_agent,
        )
        raise Unauthorized("Invalid credentials")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    access_token, refresh_token, expires_in = await _issue_tokens(
        db, user=user, ip=ip, user_agent=user_agent
    )
    user.last_login_at = _utcnow()
    await write_audit(
        db,
        action="auth.login.ok",
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return user, access_token, refresh_token, expires_in


async def _issue_tokens(
    db: AsyncSession,
    *,
    user: User,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, str, int]:
    settings = get_settings()
    refresh_token = generate_opaque_token(32)
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        ip=ip,
        user_agent=user_agent,
        expires_at=_utcnow() + timedelta(seconds=settings.auth.jwt_refresh_ttl_seconds),
        last_used_at=_utcnow(),
    )
    db.add(session)
    await db.flush()
    access_token = create_access_token(str(user.id))
    return access_token, refresh_token, settings.auth.jwt_access_ttl_seconds


async def refresh_tokens(
    db: AsyncSession,
    *,
    refresh_token: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, str, str, int]:
    settings = get_settings()
    token_hash = hash_token(refresh_token)
    now = _utcnow()

    session = await db.scalar(
        select(Session).where(Session.refresh_token_hash == token_hash)
    )
    if session is None or session.revoked_at is not None or session.expires_at < now:
        raise Unauthorized("Invalid or expired refresh token")

    user = await db.scalar(select(User).where(User.id == session.user_id))
    if user is None or not user.is_active:
        raise Unauthorized("User inactive")

    # Rotate: revoke old, create new
    session.revoked_at = now
    new_refresh = generate_opaque_token(32)
    new_session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(new_refresh),
        ip=ip,
        user_agent=user_agent,
        expires_at=now + timedelta(seconds=settings.auth.jwt_refresh_ttl_seconds),
        last_used_at=now,
    )
    db.add(new_session)
    await db.flush()
    return (
        user,
        create_access_token(str(user.id)),
        new_refresh,
        settings.auth.jwt_access_ttl_seconds,
    )


async def revoke_session_by_token(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_token(refresh_token)
    await db.execute(
        update(Session)
        .where(Session.refresh_token_hash == token_hash)
        .values(revoked_at=_utcnow())
    )


# ─── Magic links ────────────────────────────────────────────


async def issue_magic_link(
    db: AsyncSession,
    *,
    email: str,
    purpose: str = "login",
) -> str:
    settings = get_settings()
    token = generate_opaque_token(32)
    expires_at = _utcnow() + timedelta(seconds=settings.auth.magic_link_ttl_seconds)
    db.add(
        MagicLink(
            token_hash=hash_token(token),
            email=email.lower(),
            purpose=purpose,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return token


async def send_magic_link(db: AsyncSession, *, email: str) -> None:
    """Issue + email a magic link for login. Always 'succeeds' to avoid email enumeration."""
    settings = get_settings()
    user = await db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not user.is_active:
        logger.info("auth.magic_link.skipped_unknown_email", email=email)
        return
    token = await issue_magic_link(db, email=email, purpose="login")
    link = f"{settings.app.base_url}/magic?token={token}"
    await send_templated(
        email, "magic_link.html.j2", link=link, ttl_minutes=settings.auth.magic_link_ttl_seconds // 60
    )


async def consume_magic_link(
    db: AsyncSession,
    *,
    token: str,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, str, str, int]:
    token_hash = hash_token(token)
    now = _utcnow()
    ml = await db.scalar(
        select(MagicLink).where(
            MagicLink.token_hash == token_hash,
            MagicLink.purpose == "login",
        )
    )
    if ml is None or ml.used_at is not None or ml.expires_at < now:
        raise Unauthorized("Invalid or expired magic link")

    user = await db.scalar(select(User).where(User.email == ml.email))
    if user is None or not user.is_active:
        raise Unauthorized("User not found")

    ml.used_at = now
    access, refresh, expires_in = await _issue_tokens(db, user=user, ip=ip, user_agent=user_agent)
    user.last_login_at = now
    await write_audit(
        db,
        action="auth.magic_link.login",
        actor_user_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    return user, access, refresh, expires_in
