"""Auth API + page routes.

Pages (HTML):
  GET /auth/login
  GET /auth/signup
  GET /auth/role-selection
  GET /auth/google           -> redirect to Google
  GET /auth/google/callback  -> finish OAuth, set session, redirect
  POST /auth/logout (also accessible as GET for convenience)

JSON API:
  POST /api/auth/signup        {name,email,password,role}
  POST /api/auth/login         {email,password}
  POST /api/auth/role-selection {role}
  GET  /api/auth/me
  POST /api/auth/logout
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from backend.store import personas_repo, users_repo
from backend.store.models import TeacherPersona, User
from backend.services.supabase_analytics import track_user_login
from config import BASE_DIR

from .admin import is_admin_user
from .dependencies import current_user, require_user
from .oauth import google_enabled, oauth_client
from .passwords import hash_password, verify_password
from .sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    issue_session,
    read_session,
    set_session_cookie,
)

logger = logging.getLogger("parallea.auth.routes")
router = APIRouter()

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ALLOWED_ROLES = {"teacher", "student"}
MIN_PASSWORD_LEN = 8

AUTH_PAGES = {
    "login": BASE_DIR / "auth-login.html",
    "signup": BASE_DIR / "auth-signup.html",
    "role-selection": BASE_DIR / "auth-role-selection.html",
}


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="invalid email format")
    return email


def _validate_password(password: str) -> str:
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"password must be at least {MIN_PASSWORD_LEN} characters")
    return password


def _validate_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="role must be 'teacher' or 'student'")
    return role


def _post_login_redirect(role: str) -> str:
    if role == "teacher":
        return "/teacher/dashboard"
    if role == "admin":
        return "/teacher/dashboard"
    return "/student/personas"


def _post_login_redirect_for_user(user: dict[str, Any]) -> str:
    if is_admin_user(user):
        return "/admin"
    return _post_login_redirect(user.get("role", "student"))


def _user_public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email"),
        "image": user.get("image"),
        "role": user.get("role"),
        "auth_provider": user.get("auth_provider"),
        "isAdmin": is_admin_user(user),
    }


def _ensure_teacher_persona(user: dict[str, Any]) -> None:
    if user.get("role") != "teacher":
        return
    if personas_repo.first_where(teacher_id=user["id"]):
        return
    personas_repo.create(
        TeacherPersona(
            teacher_id=user["id"],
            teacher_name=user.get("name", "Teacher"),
            profession="",
            avatar_preset_id="girl_1",
        )
    )


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


def _serve_page(slug: str) -> HTMLResponse:
    path = AUTH_PAGES.get(slug)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"auth page '{slug}' missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("/auth/login", response_class=HTMLResponse)
def page_login(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse(_post_login_redirect_for_user(user))
    return _serve_page("login")


@router.get("/auth/signup", response_class=HTMLResponse)
def page_signup(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse(_post_login_redirect_for_user(user))
    return _serve_page("signup")


@router.get("/auth/role-selection", response_class=HTMLResponse)
def page_role(request: Request):
    user = current_user(request)
    if user and (user.get("role") in ALLOWED_ROLES or is_admin_user(user)):
        return RedirectResponse(_post_login_redirect_for_user(user))
    # User must be partially authed via cookie to be on this page; if not,
    # send them back to login.
    sess = read_session(request.cookies.get(SESSION_COOKIE))
    if not sess:
        return RedirectResponse("/auth/login")
    return _serve_page("role-selection")


# ---------------------------------------------------------------------------
# JSON API: signup / login / logout / me / role
# ---------------------------------------------------------------------------


@router.post("/api/auth/signup")
def api_signup(request: Request, payload: dict[str, Any] = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    email = _validate_email(payload.get("email", ""))
    password = _validate_password(payload.get("password", ""))
    confirm = payload.get("confirm_password") or payload.get("confirmPassword")
    if confirm is not None and confirm != password:
        raise HTTPException(status_code=400, detail="passwords do not match")
    role = _validate_role(payload.get("role", ""))

    existing = users_repo.first_where(email=email)
    if existing:
        # Allow claiming a "seed" account by setting the password.
        if existing.get("auth_provider") == "seed" and not existing.get("password_hash"):
            users_repo.update(
                existing["id"],
                {
                    "name": name or existing.get("name") or "Teacher",
                    "role": role,
                    "auth_provider": "email",
                    "password_hash": hash_password(password),
                },
            )
            user = users_repo.get(existing["id"])
        else:
            raise HTTPException(status_code=409, detail="account with this email already exists")
    else:
        user = users_repo.create(
            User(
                name=name,
                email=email,
                role=role,
                auth_provider="email",
                password_hash=hash_password(password),
            )
        )

    _ensure_teacher_persona(user)
    token = issue_session(user["id"], user["role"])
    response = JSONResponse({"user": _user_public(user), "redirect": _post_login_redirect_for_user(user)})
    set_session_cookie(response, token, request)
    track_user_login(user)
    return response


@router.post("/api/auth/login")
def api_login(request: Request, payload: dict[str, Any] = Body(...)):
    email = _validate_email(payload.get("email", ""))
    password = payload.get("password") or ""
    user = users_repo.first_where(email=email)
    if not user or not verify_password(password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="invalid email or password")
    if not is_admin_user(user) and user.get("role") not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="account requires role selection")
    token = issue_session(user["id"], user["role"])
    response = JSONResponse({"user": _user_public(user), "redirect": _post_login_redirect_for_user(user)})
    set_session_cookie(response, token, request)
    track_user_login(user)
    return response


@router.post("/api/auth/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@router.get("/auth/logout")
def page_logout():
    response = RedirectResponse("/auth/login", status_code=302)
    clear_session_cookie(response)
    return response


@router.get("/api/auth/me")
def api_me(user: Optional[dict] = Depends(current_user)):
    if not user:
        return JSONResponse({"user": None, "isAdmin": False}, status_code=200)
    public_user = _user_public(user)
    return {"user": public_user, "isAdmin": public_user["isAdmin"]}


@router.post("/api/auth/role-selection")
def api_role_selection(
    request: Request,
    payload: dict[str, Any] = Body(...),
):
    sess = read_session(request.cookies.get(SESSION_COOKIE))
    if not sess:
        raise HTTPException(status_code=401, detail="authentication required")
    user = users_repo.get(sess["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    role = _validate_role(payload.get("role", ""))
    users_repo.update(user["id"], {"role": role})
    user = users_repo.get(user["id"])
    _ensure_teacher_persona(user)
    token = issue_session(user["id"], user["role"])
    response = JSONResponse({"user": _user_public(user), "redirect": _post_login_redirect_for_user(user)})
    set_session_cookie(response, token, request)
    return response


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@router.get("/auth/google")
async def google_start(request: Request):
    if not google_enabled():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    client = oauth_client()
    if not client:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    redirect_uri = str(request.url_for("google_callback"))
    return await client.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    if not google_enabled():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    client = oauth_client()
    if not client:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    try:
        token = await client.google.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Google OAuth callback failed: %s", exc)
        return RedirectResponse("/auth/login?error=google_failed")

    info = token.get("userinfo") or {}
    if not info:
        try:
            info = await client.google.parse_id_token(request, token)
        except Exception:  # noqa: BLE001
            info = {}

    sub = info.get("sub")
    email = (info.get("email") or "").lower().strip()
    if not sub or not email:
        return RedirectResponse("/auth/login?error=google_no_email")

    user = users_repo.first_where(google_sub=sub) or users_repo.first_where(email=email)
    fields_to_update: dict[str, Any] = {}
    if user is None:
        user = users_repo.create(
            User(
                name=info.get("name") or email.split("@")[0],
                email=email,
                image=info.get("picture"),
                role="",  # forces role-selection
                auth_provider="google",
                google_sub=sub,
            )
        )
    else:
        if not user.get("google_sub"):
            fields_to_update["google_sub"] = sub
        if user.get("auth_provider") in {"seed", "google"}:
            fields_to_update["auth_provider"] = "google"
        if info.get("picture") and not user.get("image"):
            fields_to_update["image"] = info["picture"]
        if info.get("name") and not user.get("name"):
            fields_to_update["name"] = info["name"]
        if fields_to_update:
            users_repo.update(user["id"], fields_to_update)
            user = users_repo.get(user["id"])

    track_user_login(user)

    if not is_admin_user(user) and user.get("role") not in ALLOWED_ROLES:
        # Issue a partial session so role-selection can identify the user.
        token_partial = issue_session(user["id"], user.get("role") or "")
        response = RedirectResponse("/auth/role-selection")
        set_session_cookie(response, token_partial, request)
        return response

    _ensure_teacher_persona(user)
    token_full = issue_session(user["id"], user["role"])
    response = RedirectResponse(_post_login_redirect_for_user(user))
    set_session_cookie(response, token_full, request)
    return response


# Public capability endpoint so the auth UI can hide the Google button when
# credentials aren't configured locally.
@router.get("/api/auth/providers")
def api_providers():
    return {"google": google_enabled()}
