"""Invites and access requests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import AccessRequest, Invite, Membership, Organization, User
from zk2.config import get_settings
from zk2.core.audit import write_audit
from zk2.core.email import send_templated
from zk2.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from zk2.core.rate_limit import hit as rate_limit_hit
from zk2.core.security import generate_opaque_token, hash_password, hash_token

logger = structlog.get_logger()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _check_email_domain(email: str) -> None:
    allowed = get_settings().auth.allowed_email_domains_list
    if allowed is None:
        return
    domain = email.split("@", 1)[-1].lower()
    if domain not in allowed:
        raise ValidationError(f"Email domain '{domain}' is not allowed")


# ─── Access requests ────────────────────────────────────────


async def create_access_request(
    db: AsyncSession,
    redis: Redis,
    *,
    email: str,
    message: str | None,
    ip: str | None,
) -> AccessRequest:
    settings = get_settings()
    _check_email_domain(email)

    await rate_limit_hit(
        redis,
        key=f"rl:access_req:email:{email.lower()}",
        limit=settings.auth.rate_limit_access_request_per_hour_per_email,
        window_seconds=3600,
        error_message="Too many requests for this email; please try again later",
    )
    await rate_limit_hit(
        redis,
        key=f"rl:access_req:ip:{ip or 'unknown'}",
        limit=settings.auth.rate_limit_access_request_per_hour_per_ip,
        window_seconds=3600,
        error_message="Too many requests from this IP",
    )

    req = AccessRequest(email=email.lower(), message=message, ip=ip)
    db.add(req)
    await db.flush()

    super_admin_email = settings.super_admin.email
    if super_admin_email:
        try:
            await send_templated(
                super_admin_email,
                "access_request_admin.html.j2",
                email=email,
                message=message or "",
                admin_url=f"{settings.app.base_url}/admin/access-requests",
            )
        except Exception:
            logger.exception("access_request.notify_admin_failed")

    await write_audit(
        db,
        action="access_request.created",
        payload={"email": email, "request_id": req.id},
        ip=ip,
    )
    return req


async def decide_access_request(
    db: AsyncSession,
    *,
    request_id: int,
    decided_by: User,
    approve: bool,
    reason: str | None = None,
    create_org_name: str | None = None,
) -> AccessRequest | Invite:
    req = await db.scalar(select(AccessRequest).where(AccessRequest.id == request_id))
    if req is None:
        raise NotFoundError("Access request not found")
    if req.status != "pending":
        raise ConflictError(f"Request already {req.status}")

    req.status = "approved" if approve else "rejected"
    req.decided_by_user_id = decided_by.id
    req.decided_at = _utcnow()
    req.decision_reason = reason

    if not approve:
        await write_audit(
            db,
            action="access_request.rejected",
            actor_user_id=decided_by.id,
            payload={"request_id": req.id, "email": req.email, "reason": reason},
        )
        return req

    invite = await issue_invite(
        db,
        email=req.email,
        role="owner",
        org_id=None,
        create_org_name=create_org_name or req.email.split("@", 1)[0],
        created_by=decided_by,
    )
    await write_audit(
        db,
        action="access_request.approved",
        actor_user_id=decided_by.id,
        payload={"request_id": req.id, "email": req.email, "invite_id": invite.id},
    )
    return invite


# ─── Invites ────────────────────────────────────────────────


async def issue_invite(
    db: AsyncSession,
    *,
    email: str,
    role: str,
    org_id: int | None,
    create_org_name: str | None,
    created_by: User | None,
) -> Invite:
    settings = get_settings()
    _check_email_domain(email)

    if org_id is None:
        if not create_org_name:
            raise ValidationError("Either org_id or create_org_name must be provided")
        slug = slugify(create_org_name)[:64] or generate_opaque_token(6)
        # Ensure uniqueness
        existing = await db.scalar(select(Organization).where(Organization.slug == slug))
        if existing is not None:
            slug = f"{slug}-{generate_opaque_token(4)}"
        org = Organization(slug=slug, name=create_org_name)
        db.add(org)
        await db.flush()
        org_id = org.id

    token = generate_opaque_token(32)
    invite = Invite(
        token_hash=hash_token(token),
        email=email.lower(),
        role=role,
        org_id=org_id,
        expires_at=_utcnow() + timedelta(seconds=settings.auth.invite_ttl_seconds),
        created_by_user_id=created_by.id if created_by else None,
    )
    db.add(invite)
    await db.flush()

    link = f"{settings.app.base_url}/invite/{token}"
    try:
        await send_templated(
            email,
            "invite.html.j2",
            link=link,
            ttl_days=settings.auth.invite_ttl_seconds // 86400,
        )
    except Exception:
        logger.exception("invite.email_failed", invite_id=invite.id, email=email)
        raise

    return invite


async def accept_invite(
    db: AsyncSession,
    *,
    token: str,
    password: str,
    full_name: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, Invite]:
    token_hash = hash_token(token)
    now = _utcnow()

    invite = await db.scalar(select(Invite).where(Invite.token_hash == token_hash))
    if invite is None or invite.used_at is not None or invite.expires_at < now:
        raise UnauthorizedError("Invalid or expired invite")
    if invite.org_id is None:
        raise ValidationError("Invite is not linked to an organization")

    existing_user = await db.scalar(select(User).where(User.email == invite.email))
    if existing_user is not None:
        existing_membership = await db.scalar(
            select(Membership).where(
                Membership.user_id == existing_user.id,
                Membership.org_id == invite.org_id,
            )
        )
        if existing_membership is None:
            db.add(Membership(user_id=existing_user.id, org_id=invite.org_id, role=invite.role))
        user = existing_user
    else:
        user = User(
            email=invite.email,
            password_hash=hash_password(password),
            full_name=full_name,
            email_verified_at=now,
        )
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, org_id=invite.org_id, role=invite.role))

    invite.used_at = now
    invite.used_by_user_id = user.id
    await write_audit(
        db,
        action="invite.accepted",
        actor_user_id=user.id,
        org_id=invite.org_id,
        payload={"invite_id": invite.id},
        ip=ip,
        user_agent=user_agent,
    )
    return user, invite
