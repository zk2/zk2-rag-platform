"""Unit tests for crypto primitives (no DB required, but env still loaded by autouse fixture)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from zk2.core.errors import register_exception_handlers
from zk2.core.security import (
    create_access_token,
    decode_access_token,
    decrypt,
    encrypt,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)

pytestmark = pytest.mark.unit


def test_password_roundtrip() -> None:
    h = hash_password("My$ecret123")
    assert verify_password("My$ecret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip() -> None:
    token = create_access_token("42", extra={"scope": "test"})
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["scope"] == "test"


def test_opaque_token_is_url_safe_and_unique() -> None:
    a = generate_opaque_token(32)
    b = generate_opaque_token(32)
    assert a != b
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_token_deterministic() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


def test_fernet_encrypt_decrypt() -> None:
    plaintext = "sk-very-secret"
    ct = encrypt(plaintext)
    assert ct != plaintext
    assert decrypt(ct) == plaintext


class _Payload(BaseModel):
    """Module level: a model declared inside a function is not resolvable as a body."""

    size: int
    overlap: int

    @model_validator(mode="after")
    def _fits(self) -> _Payload:
        if self.overlap >= self.size:
            msg = "overlap must be smaller than size"
            raise ValueError(msg)
        return self


def test_a_validator_error_stays_a_422() -> None:
    """A model validator raising ValueError must not become a 500.

    Pydantic puts the exception object itself into the error's `ctx`, and
    handing that straight to JSONResponse fails to serialize - so the client
    got "internal error" for what is plainly a bad request.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    async def _echo(payload: _Payload) -> dict[str, int]:
        return {"size": payload.size}

    resp = TestClient(app).post("/echo", json={"size": 10, "overlap": 10})
    assert resp.status_code == 422
    assert "overlap must be smaller" in resp.text
