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

import jwt
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

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


async def _run(ws: WebSocket, bot_id: int) -> None:
    # ─── Authenticate via first message
    first = await ws.receive_json()
    if first.get("type") != "auth":
        await _send(ws, "error", message="First message must be {type: 'auth'}")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    token = first.get("token") or ""
    org_id = first.get("org_id")
    if not token or not isinstance(org_id, int):
        await _send(ws, "error", message="auth requires 'token' and integer 'org_id'")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        await _send(ws, "error", message="Invalid token")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    sm = get_sessionmaker()
    async with sm() as db:
        user = await db.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            await _send(ws, "error", message="User inactive")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if not user.is_super_admin:
            membership = await db.scalar(
                select(Membership).where(
                    Membership.user_id == user.id, Membership.org_id == org_id
                )
            )
            if membership is None:
                await _send(ws, "error", message="No access to this organization")
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        bot = await db.scalar(select(Bot).where(Bot.id == bot_id, Bot.org_id == org_id))
        if bot is None:
            await _send(ws, "error", message="Bot not found")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await _send(ws, "ready")

    # ─── Message loop
    while True:
        try:
            data = await ws.receive_json()
        except WebSocketDisconnect:
            return
        if data.get("type") != "user_message":
            await _send(ws, "error", message="Unknown message type")
            continue
        content = (data.get("content") or "").strip()
        if not content:
            await _send(ws, "error", message="Empty content")
            continue
        conversation_id = data.get("conversation_id")

        # Use a fresh session per turn so commits land between events
        async with sm() as db:
            try:
                async for ev in stream_rag(
                    db,
                    org_id=org_id,
                    bot_id=bot_id,
                    user=user,
                    conversation_id=conversation_id,
                    user_message=content,
                ):
                    await _emit(ws, ev)
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.exception("ws.chat_failed")
                await _send(ws, "error", message=str(exc))


async def _emit(ws: WebSocket, ev: StreamEvent) -> None:
    await ws.send_json({"type": ev.kind, **ev.payload})
