"""The WebSocket session loop: handshake, expiry re-check, turn handling."""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from fastapi import WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_rag_stream import _bot_with_source, fake_llm  # noqa: F401
from zk2.chat import ws as ws_module
from zk2.config import get_settings
from zk2.core.security import create_access_token

pytestmark = pytest.mark.integration


class FakeWebSocket:
    """Feeds prepared frames, then behaves like a disconnected client."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self.sent: list[dict[str, Any]] = []
        self.close_code: int | None = None

    async def receive_text(self) -> str:
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.close_code = code

    def kinds(self) -> list[str]:
        return [m["type"] for m in self.sent]


def _expired_token(user_id: int) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "sub": str(user_id),
            "exp": time.time() - 10,
            "iat": time.time() - 900,
            "iss": settings.app.name,
        },
        settings.app.secret_key.get_secret_value(),
        algorithm="HS256",
    )


async def test_full_turn_over_the_socket(
    db: AsyncSession,
    org_owner: dict[str, Any],
    fake_llm: Any,  # noqa: F811
) -> None:
    bot_id = await _bot_with_source(db, org_id=org_owner["org_id"], user_id=org_owner["user_id"])
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"],
            },
            {"type": "user_message", "content": "alpha?"},
        ]
    )
    await ws_module._run(ws, bot_id)  # type: ignore[arg-type]

    kinds = ws.kinds()
    assert kinds[0] == "ready"
    assert "conversation" in kinds
    assert "token" in kinds
    assert kinds[-1] == "done"


async def test_unknown_bot_closes_the_socket(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"],
            }
        ]
    )
    await ws_module._run(ws, 999_999)  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION
    assert ws.sent[0]["message"] == "Bot not found"


async def test_foreign_org_is_refused(db: AsyncSession, org_owner: dict[str, Any]) -> None:
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"] + 999,
            }
        ]
    )
    await ws_module._run(ws, 1)  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION
    assert "organization" in ws.sent[0]["message"]


async def test_expired_token_is_refused_at_handshake(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": _expired_token(org_owner["user_id"]),
                "org_id": org_owner["org_id"],
            }
        ]
    )
    await ws_module._run(ws, 1)  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION
    assert ws.sent[0]["message"] == "Invalid token"


async def test_token_expiring_mid_session_ends_it(
    db: AsyncSession,
    org_owner: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fake_llm: Any,  # noqa: F811
) -> None:
    """A socket must not outlive the access token it was opened with."""
    bot_id = await _bot_with_source(db, org_id=org_owner["org_id"], user_id=org_owner["user_id"])
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"],
            },
            {"type": "user_message", "content": "alpha?"},
        ]
    )
    # Jump past the token's lifetime after the handshake succeeded
    real_time = time.time
    monkeypatch.setattr(
        ws_module.time, "time", lambda: real_time() + get_settings().auth.jwt_access_ttl_seconds + 1
    )

    await ws_module._run(ws, bot_id)  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION
    assert ws.sent[-1]["code"] == "token_expired"
    assert "token" not in ws.kinds()


async def test_unknown_frame_type_is_reported_without_closing(
    db: AsyncSession, org_owner: dict[str, Any]
) -> None:
    bot_id = await _bot_with_source(db, org_id=org_owner["org_id"], user_id=org_owner["user_id"])
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"],
            },
            {"type": "nonsense"},
        ]
    )
    await ws_module._run(ws, bot_id)  # type: ignore[arg-type]
    assert ws.sent[-1]["message"] == "Unknown message type"
    assert ws.close_code is None


async def test_empty_message_is_rejected(
    db: AsyncSession,
    org_owner: dict[str, Any],
    fake_llm: Any,  # noqa: F811
) -> None:
    bot_id = await _bot_with_source(db, org_id=org_owner["org_id"], user_id=org_owner["user_id"])
    ws = FakeWebSocket(
        [
            {
                "type": "auth",
                "token": create_access_token(str(org_owner["user_id"])),
                "org_id": org_owner["org_id"],
            },
            {"type": "user_message", "content": "   "},
        ]
    )
    await ws_module._run(ws, bot_id)  # type: ignore[arg-type]
    assert ws.sent[-1]["message"] == "Empty content"
