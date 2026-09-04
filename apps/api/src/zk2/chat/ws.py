"""WebSocket chat endpoint.

Wire protocol (JSON messages):

  client → server:
    {"type": "auth", "token": "<access-jwt>", "org_id": 1}
    {"type": "user_message", "content": "...", "conversation_id": null}

  server → client:
    {"type": "ready", "expires_in": 870}
    {"type": "conversation", "id": 123}
    {"type": "sources", "items": [...]}       # what retrieval considered
    {"type": "citations", "items": [...]}     # what the answer actually used
    {"type": "token", "delta": "..."}
    {"type": "done", "tokens_in": ..., "tokens_out": ..., "cost_usd": "0.000123", "latency_ms": 1500}
    {"type": "error", "message": "..."}

Auth is via the first message — never put tokens in the URL.

A socket outlives a single HTTP request, so the access token's expiry is
re-checked before every turn: without that, a 15-minute token would grant an
unbounded session. Incoming frames are size-capped and the socket is closed
when it sits idle.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import jwt
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import Membership, User
from zk2.bots.models import Bot
from zk2.chat.rag import StreamEvent, stream_rag
from zk2.config import get_settings
from zk2.core.db import get_sessionmaker
from zk2.core.metrics import active_websockets
from zk2.core.security import decode_access_token


@dataclass(slots=True)
class _Session:
    user: User
    org_id: int
    token_expires_at: float


logger = structlog.get_logger()
router = APIRouter(tags=["chat"])


@router.websocket("/ws/chat/{bot_id}")
async def chat_ws(ws: WebSocket, bot_id: int) -> None:
    await ws.accept()
    active_websockets.inc()
    try:
        await _run(ws, bot_id)
    except WebSocketDisconnect:
        return
    finally:
        active_websockets.dec()


async def _send(ws: WebSocket, kind: str, **payload: object) -> None:
    await ws.send_json({"type": kind, **payload})


async def _reject(ws: WebSocket, message: str) -> None:
    await _send(ws, "error", message=message)
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)


async def _receive_json(ws: WebSocket, *, timeout: float | None = None) -> dict[str, Any]:
    """Receive one frame with a size cap and an optional idle timeout."""
    max_bytes = get_settings().chat.max_ws_message_bytes
    if timeout is None:
        raw = await ws.receive_text()
    else:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=timeout)
    if len(raw.encode("utf-8")) > max_bytes:
        raise _FrameTooLargeError(max_bytes)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise _MalformedFrameError
    return parsed


class _FrameTooLargeError(Exception):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"Message exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes


class _MalformedFrameError(Exception):
    pass


async def _read_auth_frame(ws: WebSocket) -> tuple[str, int] | None:
    """Read and validate the shape of the auth handshake frame."""
    try:
        first = await _receive_json(ws, timeout=get_settings().chat.ws_idle_timeout_seconds)
    except (TimeoutError, _FrameTooLargeError, _MalformedFrameError, json.JSONDecodeError):
        await _reject(ws, "Malformed or oversized auth frame")
        return None

    if first.get("type") != "auth":
        await _reject(ws, "First message must be {type: 'auth'}")
        return None

    token = first.get("token") or ""
    org_id = first.get("org_id")
    if not token or not isinstance(org_id, int):
        await _reject(ws, "auth requires 'token' and integer 'org_id'")
        return None
    return str(token), org_id


def _decode_claims(token: str) -> tuple[int, float] | None:
    """Return (user_id, expiry) from an access token, or None if it is not usable."""
    try:
        payload = decode_access_token(token)
        return int(payload["sub"]), float(payload["exp"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None


async def _authenticate(ws: WebSocket) -> _Session | None:
    """Handle the auth handshake. Returns the session, or None if rejected."""
    frame = await _read_auth_frame(ws)
    if frame is None:
        return None
    token, org_id = frame

    claims = _decode_claims(token)
    if claims is None:
        await _reject(ws, "Invalid token")
        return None
    user_id, expires_at = claims

    sm = get_sessionmaker()
    async with sm() as db:
        user = await db.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            await _reject(ws, "User inactive")
            return None
        if not await _has_org_access(db, user=user, org_id=org_id):
            await _reject(ws, "No access to this organization")
            return None
    return _Session(user=user, org_id=org_id, token_expires_at=expires_at)


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
    ws: WebSocket, *, session: _Session, bot_id: int, data: dict[str, Any]
) -> None:
    """Run one user message end-to-end in its own DB session."""
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
                org_id=session.org_id,
                bot_id=bot_id,
                user=session.user,
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
    session = await _authenticate(ws)
    if session is None:
        return

    if not await _bot_exists(bot_id=bot_id, org_id=session.org_id):
        await _reject(ws, "Bot not found")
        return

    idle_timeout = get_settings().chat.ws_idle_timeout_seconds
    await _send(ws, "ready", expires_in=int(session.token_expires_at - time.time()))

    while True:
        try:
            data = await _receive_json(ws, timeout=idle_timeout)
        except WebSocketDisconnect:
            return
        except TimeoutError:
            await _send(ws, "error", message="Idle timeout")
            await ws.close(code=status.WS_1000_NORMAL_CLOSURE)
            return
        except _FrameTooLargeError as exc:
            await _send(ws, "error", message=str(exc))
            continue
        except (_MalformedFrameError, json.JSONDecodeError):
            await _send(ws, "error", message="Malformed frame")
            continue

        # The socket may outlive the access token; re-check on every turn
        if time.time() >= session.token_expires_at:
            await _send(ws, "error", message="Access token expired", code="token_expired")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if data.get("type") != "user_message":
            await _send(ws, "error", message="Unknown message type")
            continue
        await _handle_turn(ws, session=session, bot_id=bot_id, data=data)


async def _emit(ws: WebSocket, ev: StreamEvent) -> None:
    await ws.send_json({"type": ev.kind, **ev.payload})
