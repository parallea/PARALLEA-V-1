from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from config import (
    DATA_DIR,
    DELETE_GENERATED_MEDIA_AFTER_PLAYBACK,
    GENERATED_MEDIA_TTL_SECONDS,
    TMP_DIR,
)
from backend.services.storage_service import delete_object, safe_object_key, url_for_object
from backend.store.models import new_id, utcnow

logger = logging.getLogger("parallea.generated_media")

GENERATED_MEDIA_DB = DATA_DIR / "generated_media.json"
GENERATED_MEDIA_ROUTE_PREFIX = "/api/student/generated-media"
ALLOWED_MEDIA_TYPES = {"manim_video", "audio"}
ALLOWED_S3_PREFIXES = ("temp/manim-renders/", "temp/audio-responses/")
ALLOWED_LOCAL_ROOTS = (
    (TMP_DIR / "manim-renders").resolve(),
    (TMP_DIR / "audio-responses").resolve(),
)
_lock = threading.RLock()
_cache: list[dict[str, Any]] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _load() -> list[dict[str, Any]]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        if not GENERATED_MEDIA_DB.exists():
            _cache = []
            _flush()
            return _cache
        try:
            raw = json.loads(GENERATED_MEDIA_DB.read_text(encoding="utf-8") or "[]")
        except Exception:
            raw = []
        _cache = raw if isinstance(raw, list) else []
        return _cache


def _flush() -> None:
    assert _cache is not None
    GENERATED_MEDIA_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = GENERATED_MEDIA_DB.with_suffix(GENERATED_MEDIA_DB.suffix + ".tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, GENERATED_MEDIA_DB)


def _update(media_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        rows = _load()
        for index, row in enumerate(rows):
            if row.get("id") == media_id:
                row.update(fields)
                row["updated_at"] = utcnow()
                rows[index] = row
                _flush()
                return dict(row)
    return None


def _safe_local_path(path_text: Any) -> Path | None:
    if not path_text:
        return None
    try:
        path = Path(str(path_text)).resolve()
    except Exception:
        return None
    for root in ALLOWED_LOCAL_ROOTS:
        if path == root or root in path.parents:
            return path
    logger.warning("generated media local delete rejected outside temp roots path=%s", path)
    return None


def _safe_temp_object_key(object_key: Any) -> str:
    key = safe_object_key(*str(object_key or "").split("/"))
    if key.startswith("teacher-videos/"):
        raise ValueError("refusing to delete teacher uploaded video object")
    if not key.startswith(ALLOWED_S3_PREFIXES):
        raise ValueError(f"refusing to delete non-temporary generated media object key={key}")
    return key


def _strip_url_query(url: Any) -> str:
    try:
        parsed = urlsplit(str(url or ""))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return str(url or "").split("?", 1)[0]


def generated_media_url(media_id: str) -> str:
    return f"{GENERATED_MEDIA_ROUTE_PREFIX}/{media_id}"


def register_generated_media(
    *,
    session_id: str,
    message_id: str,
    media_type: str,
    storage_backend: str,
    local_path: str | Path | None = None,
    object_key: str | None = None,
    url: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError(f"unsupported generated media type: {media_type}")
    backend = (storage_backend or "local").strip().lower()
    if backend not in {"local", "s3"}:
        raise ValueError(f"unsupported generated media backend: {backend}")
    safe_local = str(_safe_local_path(local_path)) if backend == "local" else None
    safe_key = _safe_temp_object_key(object_key) if backend == "s3" else None
    media_id = new_id("gmed")
    created_at = _now()
    expires_at = created_at + timedelta(seconds=GENERATED_MEDIA_TTL_SECONDS)
    record = {
        "id": media_id,
        "media_id": media_id,
        "session_id": session_id or "",
        "message_id": message_id or "",
        "media_type": media_type,
        "storage_backend": backend,
        "local_path": safe_local,
        "object_key": safe_key,
        "url": url or (generated_media_url(media_id) if backend == "local" else None),
        "content_type": content_type,
        "size_bytes": size_bytes,
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "deleted_at": None,
        "status": "active",
    }
    with _lock:
        _load().append(record)
        _flush()
    logger.info(
        "generated media registered id=%s type=%s backend=%s session=%s message=%s object_key=%s local_path=%s expires_at=%s",
        media_id,
        media_type,
        backend,
        session_id,
        message_id,
        safe_key or "",
        safe_local or "",
        record["expires_at"],
    )
    cleanup_expired_generated_media()
    return dict(record)


def get_generated_media(media_id: str) -> dict[str, Any] | None:
    with _lock:
        for row in _load():
            if row.get("id") == media_id:
                return dict(row)
    return None


def get_active_generated_media(session_id: str, message_id: str, media_type: str) -> dict[str, Any] | None:
    with _lock:
        match = next(
            (
                dict(row)
                for row in _load()
                if row.get("status") == "active"
                and row.get("session_id") == session_id
                and row.get("message_id") == message_id
                and row.get("media_type") == media_type
            ),
            None,
        )
    if not match:
        return None
    expires = _parse_time(match.get("expires_at"))
    if expires and expires <= _now():
        delete_generated_media(str(match.get("id")), reason="expired_on_lookup")
        return None
    if match.get("storage_backend") == "s3" and match.get("object_key"):
        try:
            refreshed_url = url_for_object(str(match["object_key"]))
            match["url"] = refreshed_url
            _update(str(match["id"]), {"url": refreshed_url})
        except Exception as exc:  # noqa: BLE001
            logger.warning("generated media presigned URL refresh failed id=%s key=%s error=%s", match.get("id"), match.get("object_key"), exc)
    return match


def active_generated_media_for_file(media_id: str) -> dict[str, Any] | None:
    row = get_generated_media(media_id)
    if not row or row.get("status") != "active":
        return None
    expires = _parse_time(row.get("expires_at"))
    if expires and expires <= _now():
        delete_generated_media(media_id, reason="expired_on_access")
        return None
    return row


def _delete_local(row: dict[str, Any]) -> bool:
    path = _safe_local_path(row.get("local_path"))
    if not path:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("generated media local delete failed id=%s path=%s error=%s", row.get("id"), path, exc)
        return False


def _delete_s3(row: dict[str, Any]) -> bool:
    try:
        key = _safe_temp_object_key(row.get("object_key"))
        return delete_object(key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generated media s3 delete failed id=%s key=%s error=%s", row.get("id"), row.get("object_key"), exc)
        return False


def delete_generated_media(media_id: str, *, reason: str = "playback_ended") -> dict[str, Any]:
    row = get_generated_media(media_id)
    if not row:
        return {"success": True, "deleted": False, "reason": "not_found"}
    if row.get("status") == "deleted":
        return {"success": True, "deleted": False, "reason": "already_deleted", "media": row}
    backend = row.get("storage_backend")
    deleted = _delete_s3(row) if backend == "s3" else _delete_local(row)
    status = "deleted" if deleted else "delete_failed"
    updated = _update(
        media_id,
        {
            "status": status,
            "deleted_at": utcnow() if deleted else None,
            "delete_reason": reason,
        },
    )
    log_fn = logger.info if deleted else logger.warning
    log_fn(
        "generated media delete %s id=%s type=%s backend=%s reason=%s object_key=%s local_path=%s",
        "success" if deleted else "failed",
        media_id,
        row.get("media_type"),
        backend,
        reason,
        row.get("object_key") or "",
        row.get("local_path") or "",
    )
    return {"success": bool(deleted), "deleted": bool(deleted), "media": updated or row}


def delete_generated_media_by_url(url: str) -> dict[str, Any]:
    target = _strip_url_query(url)
    with _lock:
        matches = [
            row
            for row in _load()
            if row.get("status") == "active"
            and (_strip_url_query(row.get("url")) == target or str(row.get("url") or "") == str(url or ""))
        ]
    result = {"success": True, "deleted": 0, "results": []}
    for row in matches:
        item = delete_generated_media(str(row.get("id")), reason="url_delete")
        result["results"].append(item)
        if item.get("deleted"):
            result["deleted"] += 1
        if not item.get("success"):
            result["success"] = False
    return result


def delete_generated_media_for_message(session_id: str, message_id: str, media_type: str) -> dict[str, Any]:
    if not DELETE_GENERATED_MEDIA_AFTER_PLAYBACK:
        logger.info("generated media playback delete skipped by config session=%s message=%s type=%s", session_id, message_id, media_type)
        return {"success": True, "deleted": 0, "skipped": True}
    with _lock:
        matches = [
            row
            for row in _load()
            if row.get("status") == "active"
            and row.get("session_id") == session_id
            and row.get("message_id") == message_id
            and row.get("media_type") == media_type
        ]
    result = {"success": True, "deleted": 0, "results": []}
    logger.info("generated media delete requested after playback session=%s message=%s type=%s count=%s", session_id, message_id, media_type, len(matches))
    for row in matches:
        item = delete_generated_media(str(row.get("id")), reason="playback_ended")
        result["results"].append(item)
        if item.get("deleted"):
            result["deleted"] += 1
        if not item.get("success"):
            result["success"] = False
    return result


def cleanup_expired_generated_media() -> dict[str, Any]:
    now = _now()
    with _lock:
        expired = [
            row
            for row in _load()
            if row.get("status") == "active"
            and (_parse_time(row.get("expires_at")) or now) <= now
        ]
    result = {"success": True, "expired": len(expired), "deleted": 0, "results": []}
    for row in expired:
        item = delete_generated_media(str(row.get("id")), reason="ttl_expired")
        result["results"].append(item)
        if item.get("deleted"):
            result["deleted"] += 1
        if not item.get("success"):
            result["success"] = False
    if expired:
        logger.info("generated media TTL cleanup expired=%s deleted=%s success=%s", result["expired"], result["deleted"], result["success"])
    return result


def cleanup_session_generated_media(session_id: str) -> dict[str, Any]:
    with _lock:
        rows = [row for row in _load() if row.get("status") == "active" and row.get("session_id") == session_id]
    result = {"success": True, "deleted": 0, "results": []}
    for row in rows:
        item = delete_generated_media(str(row.get("id")), reason="session_cleanup")
        result["results"].append(item)
        if item.get("deleted"):
            result["deleted"] += 1
        if not item.get("success"):
            result["success"] = False
    logger.info("generated media session cleanup session=%s count=%s deleted=%s", session_id, len(rows), result["deleted"])
    return result
