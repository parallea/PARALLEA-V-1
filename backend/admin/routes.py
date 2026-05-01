from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from backend.auth.admin import admin_secret_matches, is_admin_user
from backend.auth.dependencies import current_user
from backend.services.supabase_analytics import (
    get_user_analytics,
    supabase_analytics_config_status,
)
from config import BASE_DIR

router = APIRouter()

ADMIN_PAGE = BASE_DIR / "admin.html"


def _require_admin_access(request: Request, user: Optional[dict]) -> None:
    if is_admin_user(user):
        return
    if admin_secret_matches(request):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


@router.get("/admin", response_class=HTMLResponse)
def page_admin():
    if not ADMIN_PAGE.exists():
        raise HTTPException(status_code=404, detail="admin page missing")
    return HTMLResponse(ADMIN_PAGE.read_text(encoding="utf-8"))


@router.get("/api/admin/user-analytics")
def api_admin_user_analytics(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    user: Optional[dict] = Depends(current_user),
):
    _require_admin_access(request, user)
    return {
        "analytics": get_user_analytics(limit=limit),
        "supabase": supabase_analytics_config_status(),
        "sort": ["total_sessions desc", "total_questions desc", "last_login_at desc"],
    }
