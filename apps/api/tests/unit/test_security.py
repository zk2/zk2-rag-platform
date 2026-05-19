"""Unit tests for crypto primitives (no DB required, but env still loaded by autouse fixture)."""

from __future__ import annotations

import pytest

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
