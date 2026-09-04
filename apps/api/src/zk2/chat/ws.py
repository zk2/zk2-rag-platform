"""WebSocket chat endpoint.

Wire protocol (JSON messages):

  client → server:
    {"type": "auth", "token": "<access-jwt>", "org_id": 1}
    {"type": "user_message", "content": "...", "conversation_id": null}

  server → client:
    {"type": "ready"}
    {"type": "conversation", "id": 123}
    {"type": "sources", "items": [...]}
    {"type": "token", "delta": "..."}
    {"type": "done", "tokens_in": ..., "tokens_out": ..., "cost_usd": "0.000123", "latency_ms": 1500}
    {"type": "error", "message": "..."}

Auth is via the first message — never put tokens in the URL.
"""

from __future__ import annotations

from typing import Any

import jwt
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import Membership, User
from zk2.bots.models import Bot
from zk2.chat.rag import StreamEvent, stream_rag
from zk2.core.db import get_sessionmaker
from zk2.core.security import decode_access_token

logger = structlog.get_logger()
router = APIRouter(tags=["chat"])


@router.websocket("/ws/chat/{bot_id}")
async def chat_ws(ws: WebSocket, bot_id: int) -> None:
    await ws.accept()
    try:
        await _run(ws, bot_id)
    except WebSocketDisconnect:
        return


async def _send(ws: WebSocket, kind: str, **payload: object) -> None:
    await ws.send_json({"type": kind, **payload})


async def _reject(ws: WebSocket, message: str) -> None:
    await _send(ws, "error", message=message)
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)


async def _authenticate(ws: WebSocket) -> tuple[User, int] | None:
    """Handle the auth handshake. Returns (user, org_id), or None if rejected."""
    first = await ws.receive_json()
    if first.get("type") != "auth":
        await _reject(ws, "First message must be {type: 'auth'}")
        return None

    token = first.get("token") or ""
    org_id = first.get("org_id")
    if not token or not isinstance(org_id, int):
        await _reject(ws, "auth requires 'token' and integer 'org_id'")
        return None

    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await _reject(ws, "Invalid token")
        return None

    sm = get_sessionmaker()
    async with sm() as db:
        user = await db.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            await _reject(ws, "User inactive")
            return None
        if not await _has_org_access(db, user=user, org_id=org_id):
            await _reject(ws, "No access to this organization")
            return None
    return user, org_id


async def _has_org_access(db: AsyncSession, *, user: User, org_id: int) -> bool:
    if user.is_super_admin:
        return True
    membership = await db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id)
    )
    return membership is not None


async def _bot_exists(*, bot_id: int, org_id: int) -> bool:
    sm = get_sessionmaker()
    async with sm() as db:
        bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
        return bot is not None


async def _handle_turn(
    ws: WebSocket, *, user: User, org_id: int, bot_id: int, data: dict[str, Any]
) -> None:
    """Run one user message end-to-end in its own session."""
    content = (data.get("content") or "").strip()
    if not content:
        await _send(ws, "error", message="Empty content")
        return

    sm = get_sessionmaker()
    # Fresh session per turn so commits land between events
    async with sm() as db:
        try:
            async for ev in stream_rag(
                db,
                org_id=org_id,
                bot_id=bot_id,
                user=user,
                conversation_id=data.get("conversation_id"),
                user_message=content,
            ):
                await _emit(ws, ev)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("ws.chat_failed")
            await _send(ws, "error", message=str(exc))


async def _run(ws: WebSocket, bot_id: int) -> None:
    authed = await _authenticate(ws)
    if authed is None:
        return
    user, org_id = authed

    if not await _bot_exists(bot_id=bot_id, org_id=org_id):
        await _reject(ws, "Bot not found")
        return

    await _send(ws, "ready")

    while True:
        try:
            data = await ws.receive_json()
        except WebSocketDisconnect:
            return
        if data.get("type") != "user_message":
            await _send(ws, "error", message="Unknown message type")
            continue
        await _handle_turn(ws, user=user, org_id=org_id, bot_id=bot_id, data=data)


async def _emit(ws: WebSocket, ev: StreamEvent) -> None:
    await ws.send_json({"type": ev.kind, **ev.payload})
