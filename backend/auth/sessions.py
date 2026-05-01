"""Signed-cookie session.

The cookie payload is a small JSON dict (`{user_id, role}`) signed with
itsdangerous. Stateless — no server-side session store. Rotated by changing
AUTH_SECRET. Lifetime defaults to 30 days, refreshable on each request.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import env_str

SESSION_COOKIE = "parallea_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_SESSION_SALT = "parallea-session-v1"

logger = logging.getLogger("parallea.auth")


def _serializer() -> URLSafeTimedSerializer:
    secret = env_str("AUTH_SECRET", "dev-auth-secret-change-me")
    return URLSafeTimedSerializer(secret, salt=_SESSION_SALT)


def issue_session(user_id: str, role: str, **extra: Any) -> str:
    payload = {"user_id": user_id, "role": role}
    payload.update(extra)
    return _serializer().dumps(payload)


def read_session(token: str | None, max_age: int = SESSION_MAX_AGE) -> Optional[dict[str, Any]]:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        logger.info("session expired")
        return None
    except BadSignature:
        logger.warning("session bad signature")
        return None
    if not isinstance(data, dict) or not data.get("user_id"):
        return None
    return data


def _is_local_http_request(request: Request | None) -> bool:
    if request is None:
        return False
    host = (request.url.hostname or "").lower()
    return request.url.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}


def _cookie_secure(request: Request | None = None) -> bool:
    # Allow override; default to insecure for local http dev.
    raw = env_str("PARALLEA_COOKIE_SECURE", "0").lower()
    if raw in {"1", "true", "yes"} and _is_local_http_request(request):
        return False
    return raw in {"1", "true", "yes"}


def set_session_cookie(response: Response, token: str, request: Request | None = None) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_from_request(request: Request) -> Optional[dict[str, Any]]:
    token = request.cookies.get(SESSION_COOKIE)
    return read_session(token)
