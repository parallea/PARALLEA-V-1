"""Backend-only admin authorization helpers."""
from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import Request

from config import env_str

logger = logging.getLogger("parallea.auth.admin")


def configured_admin_emails() -> set[str]:
    raw = env_str("ADMIN_EMAILS", "")
    return {
        email.strip().lower()
        for email in raw.split(",")
        if email.strip()
    }


def configured_admin_email_count() -> int:
    return len(configured_admin_emails())


def admin_secret_fallback_enabled() -> bool:
    return bool(env_str("ADMIN_SECRET", ""))


def is_admin_user(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return False
    if user.get("role") == "admin":
        return True
    email = (user.get("email") or "").strip().lower()
    return bool(email and email in configured_admin_emails())


def admin_secret_matches(request: Request) -> bool:
    admin_secret = env_str("ADMIN_SECRET", "")
    if not admin_secret:
        return False
    supplied = request.headers.get("x-admin-secret") or ""
    return bool(supplied and secrets.compare_digest(supplied, admin_secret))


def log_admin_auth_status() -> None:
    logger.info("configured admin emails: %s", configured_admin_email_count())
    logger.info("ADMIN_SECRET fallback enabled: %s", admin_secret_fallback_enabled())
