"""Crypto primitives: passwords, JWT, opaque tokens, symmetric encryption."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from zk2.config import get_settings

_hasher = PasswordHasher()


# ─── Passwords ──────────────────────────────────────────────


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


# ─── JWT (access tokens) ────────────────────────────────────


def create_access_token(subject: str, *, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.auth.jwt_access_ttl_seconds)).timestamp()),
        "iss": settings.app.name,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.app.secret_key.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.app.secret_key.get_secret_value(),
        algorithms=["HS256"],
        issuer=settings.app.name,
    )


# ─── Opaque tokens (refresh, magic links, invites) ──────────


def generate_opaque_token(nbytes: int = 32) -> str:
    """URL-safe random token."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hash for storing token references (we never store raw)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ─── Symmetric encryption (provider API keys at rest) ───────


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(settings.app.secret_key.get_secret_value().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
