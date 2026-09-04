"""WebSocket frame handling and the auth handshake.

These are the guards that keep a long-lived socket from becoming a way around
token expiry or message limits, so they are tested without a real connection.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi import status

from zk2.chat.ws import (
    _decode_claims,
    _FrameTooLargeError,
    _MalformedFrameError,
    _read_auth_frame,
    _receive_json,
)
from zk2.config import get_settings
from zk2.core.security import create_access_token

pytestmark = pytest.mark.unit


class FakeWebSocket:
    """Just enough WebSocket for the frame helpers."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.close_code: int | None = None

    async def receive_text(self) -> str:
        return self._frames.pop(0)

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def close(self, code: int) -> None:
        self.close_code = code


async def test_receive_json_parses_object() -> None:
    ws = FakeWebSocket([json.dumps({"type": "auth"})])
    assert await _receive_json(ws) == {"type": "auth"}  # type: ignore[arg-type]


async def test_oversized_frame_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().chat, "max_ws_message_bytes", 64)
    ws = FakeWebSocket([json.dumps({"type": "user_message", "content": "x" * 200})])
    with pytest.raises(_FrameTooLargeError):
        await _receive_json(ws)  # type: ignore[arg-type]


async def test_non_object_frame_rejected() -> None:
    ws = FakeWebSocket(["[1, 2, 3]"])
    with pytest.raises(_MalformedFrameError):
        await _receive_json(ws)  # type: ignore[arg-type]


async def test_invalid_json_rejected() -> None:
    ws = FakeWebSocket(["{definitely not json"])
    with pytest.raises(json.JSONDecodeError):
        await _receive_json(ws)  # type: ignore[arg-type]


def test_decode_claims_returns_subject_and_expiry() -> None:
    token = create_access_token("42")
    claims = _decode_claims(token)
    assert claims is not None
    user_id, expires_at = claims
    assert user_id == 42
    assert expires_at > time.time()


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_decode_claims_rejects_bad_tokens(token: str) -> None:
    assert _decode_claims(token) is None


def test_decode_claims_rejects_foreign_signature() -> None:
    import jwt

    forged = jwt.encode({"sub": "1", "exp": time.time() + 60}, "not-our-secret", algorithm="HS256")
    assert _decode_claims(forged) is None


async def test_auth_frame_must_be_auth_type() -> None:
    ws = FakeWebSocket([json.dumps({"type": "user_message", "content": "hi"})])
    assert await _read_auth_frame(ws) is None  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION
    assert ws.sent[0]["type"] == "error"


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "auth", "org_id": 1},  # no token
        {"type": "auth", "token": "t"},  # no org
        {"type": "auth", "token": "t", "org_id": "1"},  # org not an int
    ],
)
async def test_auth_frame_requires_token_and_org(frame: dict[str, Any]) -> None:
    ws = FakeWebSocket([json.dumps(frame)])
    assert await _read_auth_frame(ws) is None  # type: ignore[arg-type]
    assert ws.close_code == status.WS_1008_POLICY_VIOLATION


async def test_auth_frame_accepted() -> None:
    ws = FakeWebSocket([json.dumps({"type": "auth", "token": "tok", "org_id": 7})])
    assert await _read_auth_frame(ws) == ("tok", 7)  # type: ignore[arg-type]
    assert ws.close_code is None
