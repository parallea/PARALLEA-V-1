"""Supabase-backed usage analytics.

This module is intentionally best-effort: analytics must never block login,
session creation, or question answering. The Supabase client is initialized
lazily so local development keeps working when the dependency or env vars are
missing.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger("parallea.supabase_analytics")

_client: Any = None
_client_lock = threading.RLock()
_client_import_failed = False
_events_table_available = True
_processed_question_ids: set[tuple[str, str, str]] = set()
_inflight_question_ids: set[tuple[str, str, str]] = set()
_processed_question_ids_lock = threading.RLock()

SAFE_ANALYTICS_FIELDS = (
    "email",
    "name",
    "total_sessions",
    "total_questions",
    "first_login_at",
    "last_login_at",
    "last_session_at",
    "last_question_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def supabase_analytics_config_status() -> dict[str, bool]:
    url_present = bool(SUPABASE_URL)
    key_present = bool(SUPABASE_SERVICE_ROLE_KEY)
    return {
        "enabled": url_present and key_present,
        "supabase_url_present": url_present,
        "supabase_service_role_key_present": key_present,
    }


def log_supabase_analytics_status() -> None:
    status = supabase_analytics_config_status()
    logger.info("Supabase analytics enabled: %s", status["enabled"])
    logger.info("SUPABASE_URL present: %s", status["supabase_url_present"])
    logger.info("SUPABASE_SERVICE_ROLE_KEY present: %s", status["supabase_service_role_key_present"])


def is_supabase_analytics_enabled() -> bool:
    return supabase_analytics_config_status()["enabled"]


def _get_client() -> Optional[Any]:
    global _client, _client_import_failed
    if not is_supabase_analytics_enabled():
        return None
    if _client is not None:
        return _client
    if _client_import_failed:
        return None
    with _client_lock:
        if _client is not None:
            return _client
        try:
            from supabase import create_client  # type: ignore
        except Exception as exc:  # noqa: BLE001
            _client_import_failed = True
            logger.warning("Supabase analytics disabled; supabase package import failed: %s", exc)
            return None
        try:
            _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Supabase analytics client initialization failed: %s", exc)
            return None
        return _client


def _response_data(response: Any) -> Any:
    if response is None:
        return None
    if hasattr(response, "data"):
        return response.data
    if isinstance(response, dict):
        return response.get("data")
    return None


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _analytics_identity(user: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if not isinstance(user, dict):
        return None
    email = (user.get("email") or "").strip().lower()
    if not email:
        return None
    return _compact(
        {
            "user_id": user.get("id"),
            "email": email,
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url") or user.get("image"),
            "provider": user.get("auth_provider") or user.get("provider") or "google",
        }
    )


def _select_analytics_row(client: Any, email: str, columns: str = "*") -> Optional[dict[str, Any]]:
    response = client.table("user_analytics").select(columns).eq("email", email).limit(1).execute()
    rows = _response_data(response) or []
    if rows:
        return rows[0]
    return None


def _insert_analytics_row(client: Any, identity: dict[str, Any], extra: dict[str, Any]) -> None:
    payload = dict(identity)
    payload.update(extra)
    client.table("user_analytics").insert(payload).execute()


def _update_analytics_row(client: Any, email: str, fields: dict[str, Any]) -> None:
    client.table("user_analytics").update(fields).eq("email", email).execute()


def _ensure_user_row(client: Any, identity: dict[str, Any]) -> dict[str, Any]:
    email = identity["email"]
    row = _select_analytics_row(client, email)
    if row:
        return row
    try:
        _insert_analytics_row(client, identity, {"updated_at": _now_iso()})
    except Exception:  # noqa: BLE001
        # A concurrent request may have inserted the same email. Fetch it once
        # before letting the caller's outer error handling log a real failure.
        row = _select_analytics_row(client, email)
        if row:
            return row
        raise
    return _select_analytics_row(client, email) or {}


def _track_event(
    client: Any,
    event_name: str,
    identity: dict[str, Any],
    *,
    session_id: str | None = None,
    persona_id: str | None = None,
    event_properties: dict[str, Any] | None = None,
) -> None:
    global _events_table_available
    if not _events_table_available:
        return
    payload = _compact(
        {
            "user_id": identity.get("user_id"),
            "email": identity.get("email"),
            "session_id": session_id,
            "persona_id": persona_id,
            "event_name": event_name,
            "event_properties": event_properties or {},
        }
    )
    try:
        client.table("user_events").insert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        _events_table_available = False
        logger.warning("Supabase analytics event tracking disabled; user_events insert failed: %s", exc)


def _claim_question_tracking(
    client: Any,
    email: str,
    session_id: str,
    message_id: str | None,
) -> tuple[bool, tuple[str, str, str] | None]:
    if not message_id:
        return True, None
    key = (email, session_id, str(message_id))
    with _processed_question_ids_lock:
        if key in _processed_question_ids or key in _inflight_question_ids:
            return False, key
    if _events_table_available:
        try:
            response = (
                client.table("user_events")
                .select("id")
                .eq("email", email)
                .eq("session_id", session_id)
                .eq("event_name", "question_asked")
                .contains("event_properties", {"message_id": str(message_id)})
                .limit(1)
                .execute()
            )
            if _response_data(response):
                with _processed_question_ids_lock:
                    _processed_question_ids.add(key)
                return False, key
        except Exception:  # noqa: BLE001
            logger.debug("Supabase analytics question retry lookup skipped", exc_info=True)
    with _processed_question_ids_lock:
        if key in _processed_question_ids or key in _inflight_question_ids:
            return False, key
        _inflight_question_ids.add(key)
    return True, key


def _finish_question_tracking(key: tuple[str, str, str] | None, success: bool) -> None:
    if not key:
        return
    with _processed_question_ids_lock:
        _inflight_question_ids.discard(key)
        if success:
            _processed_question_ids.add(key)


def track_user_login(user: dict[str, Any] | None) -> None:
    client = _get_client()
    identity = _analytics_identity(user)
    if not client or not identity:
        return
    try:
        email = identity["email"]
        now = _now_iso()
        row = _select_analytics_row(client, email)
        if row:
            fields = dict(identity)
            fields.pop("email", None)
            fields.update({"last_login_at": now, "updated_at": now})
            _update_analytics_row(client, email, fields)
        else:
            _insert_analytics_row(
                client,
                identity,
                {
                    "total_sessions": 0,
                    "total_questions": 0,
                    "first_login_at": now,
                    "last_login_at": now,
                    "updated_at": now,
                },
            )
        _track_event(client, "user_logged_in", identity, event_properties={"provider": identity.get("provider")})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Supabase analytics login tracking failed: %s", exc)


def track_session_started(user: dict[str, Any] | None, session_id: str, persona_id: str | None = None) -> None:
    client = _get_client()
    identity = _analytics_identity(user)
    if not client or not identity or not session_id:
        return
    try:
        email = identity["email"]
        now = _now_iso()
        row = _ensure_user_row(client, identity)
        current = int((row or {}).get("total_sessions") or 0)
        fields = dict(identity)
        fields.pop("email", None)
        fields.update({"total_sessions": current + 1, "last_session_at": now, "updated_at": now})
        _update_analytics_row(client, email, fields)
        _track_event(client, "session_started", identity, session_id=session_id, persona_id=persona_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Supabase analytics session tracking failed: %s", exc)


def track_question_asked(
    user: dict[str, Any] | None,
    session_id: str,
    question_text: str | None = None,
    persona_id: str | None = None,
    message_id: str | None = None,
) -> None:
    cleaned_question = (question_text or "").strip()
    if not cleaned_question:
        return
    client = _get_client()
    identity = _analytics_identity(user)
    if not client or not identity or not session_id:
        return
    should_track, question_key = _claim_question_tracking(client, identity["email"], session_id, message_id)
    if not should_track:
        logger.info("Supabase analytics question retry skipped session=%s message_id=%s", session_id, message_id)
        return
    try:
        email = identity["email"]
        now = _now_iso()
        row = _ensure_user_row(client, identity)
        current = int((row or {}).get("total_questions") or 0)
        fields = dict(identity)
        fields.pop("email", None)
        fields.update({"total_questions": current + 1, "last_question_at": now, "updated_at": now})
        _update_analytics_row(client, email, fields)
        event_properties = {"question_length": len(cleaned_question)}
        if message_id:
            event_properties["message_id"] = str(message_id)
        _track_event(
            client,
            "question_asked",
            identity,
            session_id=session_id,
            persona_id=persona_id,
            event_properties=event_properties,
        )
        _finish_question_tracking(question_key, success=True)
    except Exception as exc:  # noqa: BLE001
        _finish_question_tracking(question_key, success=False)
        logger.exception("Supabase analytics question tracking failed: %s", exc)


def get_user_analytics(limit: int = 100) -> list[dict[str, Any]]:
    client = _get_client()
    if not client:
        return []
    limit = max(1, min(int(limit or 100), 500))
    try:
        response = (
            client.table("user_analytics")
            .select(",".join(SAFE_ANALYTICS_FIELDS))
            .order("total_sessions", desc=True)
            .order("total_questions", desc=True)
            .order("last_login_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = _response_data(response) or []
    except Exception as exc:  # noqa: BLE001
        logger.exception("Supabase analytics admin query failed: %s", exc)
        return []
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            safe_rows.append({field: row.get(field) for field in SAFE_ANALYTICS_FIELDS})
    return safe_rows
