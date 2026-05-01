"""Google OAuth wiring (lazy — only enabled when env vars are set).

Authlib needs Starlette's SessionMiddleware mounted on the app for the
state/CSRF cookie. `register_oauth_state_middleware` does that.
"""
from __future__ import annotations

import logging
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from config import env_str

logger = logging.getLogger("parallea.auth.oauth")


def google_enabled() -> bool:
    return bool(env_str("GOOGLE_CLIENT_ID") and env_str("GOOGLE_CLIENT_SECRET"))


def build_oauth() -> Optional[OAuth]:
    if not google_enabled():
        return None
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=env_str("GOOGLE_CLIENT_ID"),
        client_secret=env_str("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


_oauth_singleton: Optional[OAuth] = None


def oauth_client() -> Optional[OAuth]:
    global _oauth_singleton
    if _oauth_singleton is None and google_enabled():
        _oauth_singleton = build_oauth()
    return _oauth_singleton


def register_oauth_state_middleware(app: FastAPI) -> None:
    secret = env_str("AUTH_SECRET", "dev-auth-secret-change-me")
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="parallea_oauth_state")
