"""FastAPI dependency helpers for auth.

Use `current_user` to read the authenticated user (or None).
Use `require_user`, `require_teacher`, `require_student` to gate endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, status

from backend.store import users_repo

from .admin import is_admin_user
from .sessions import session_from_request


def current_user(request: Request) -> Optional[dict]:
    sess = session_from_request(request)
    if not sess:
        return None
    user = users_repo.get(sess["user_id"])
    if not user:
        return None
    # Trust the role on the user row, not the cookie (defense in depth).
    return user


def optional_user(request: Request) -> Optional[dict]:
    return current_user(request)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def require_role(*allowed_roles: str):
    def _dep(request: Request) -> dict:
        user = require_user(request)
        if user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{user.get('role')}' not permitted",
            )
        return user
    return _dep


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


require_teacher = require_role("teacher", "admin")
require_student = require_role("student", "admin")
