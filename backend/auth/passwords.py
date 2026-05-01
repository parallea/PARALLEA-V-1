"""Bcrypt password helpers.

We use the `bcrypt` package directly. `passlib` was tried first but has a
known incompat with bcrypt 4.x at the time of writing.

Bcrypt limits inputs to 72 bytes; we truncate explicitly so users with
longer passwords still get a deterministic result. The truncation is
documented in the README and matches what most web frameworks do.
"""
from __future__ import annotations

import bcrypt

_MAX_BYTES = 72


def _to_bytes(value: str) -> bytes:
    raw = (value or "").encode("utf-8")
    return raw[:_MAX_BYTES]


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("password is required")
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
