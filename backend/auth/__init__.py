"""Authentication module: bcrypt passwords + signed-cookie sessions + Google OAuth.

- Email/password sign-up/login is the always-available path.
- Google OAuth is enabled when GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are set.
- Sessions are stateless: a signed JSON token in an HttpOnly cookie.
"""
from .dependencies import (
    current_user,
    optional_user,
    require_role,
    require_student,
    require_teacher,
    require_user,
)
from .passwords import hash_password, verify_password
from .routes import router as auth_router
from .sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    issue_session,
    read_session,
    set_session_cookie,
)

__all__ = [
    "auth_router",
    "current_user",
    "optional_user",
    "require_user",
    "require_role",
    "require_teacher",
    "require_student",
    "hash_password",
    "verify_password",
    "SESSION_COOKIE",
    "issue_session",
    "read_session",
    "set_session_cookie",
    "clear_session_cookie",
]
