"""Teacher-facing pages + JSON API.

Pages (HTML, require authenticated teacher):
  GET /teacher/dashboard
  GET /teacher/upload
  GET /teacher/videos/{video_id}

API (JSON, require authenticated teacher):
  GET   /api/teacher/persona
  PATCH /api/teacher/persona
  POST  /api/teacher/avatar
  POST  /api/teacher/voice
  POST  /api/teacher/videos/upload
  GET   /api/teacher/videos
  GET   /api/teacher/videos/{video_id}
  POST  /api/teacher/videos/{video_id}/reprocess
  DELETE /api/teacher/videos/{video_id}
  GET   /api/teacher/roadmaps
  GET   /api/teacher/stats
  GET   /api/teacher/persona/prompts
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from backend.auth.dependencies import current_user, require_teacher
from backend.services.persona_pipeline import process_teacher_video_sync
from backend.services.video_assets import extract_thumbnail, safe_video_filename
from backend.store import (
    persona_prompts_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    sessions_repo,
    videos_repo,
)
from backend.store.models import (
    PersonaPromptVersion,
    TeacherPersona,
    TeacherVideo,
)
from config import AVATAR_PRESETS, BASE_DIR, DATA_DIR, THUMBNAILS_DIR, UPLOADS_DIR

logger = logging.getLogger("parallea.teacher")
router = APIRouter()

TEACHER_PAGES = {
    "dashboard": BASE_DIR / "teacher-dashboard.html",
    "upload": BASE_DIR / "teacher-upload.html",
    "video": BASE_DIR / "teacher-video.html",
    "roadmaps": BASE_DIR / "teacher-roadmaps.html",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serve_page(slug: str) -> HTMLResponse:
    path = TEACHER_PAGES.get(slug)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"teacher page '{slug}' missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))


def _gate_html(request: Request) -> dict | None:
    user = current_user(request)
    if not user:
        return None
    if user.get("role") not in {"teacher", "admin"}:
        return None
    return user


def _ensure_persona(user: dict[str, Any]) -> dict[str, Any]:
    persona = personas_repo.first_where(teacher_id=user["id"])
    if persona:
        return persona
    return personas_repo.create(
        TeacherPersona(
            teacher_id=user["id"],
            teacher_name=user.get("name") or "Teacher",
            avatar_preset_id="girl_1",
        )
    )


def _persona_public(persona: dict[str, Any]) -> dict[str, Any]:
    avatar_preset_id = persona.get("avatar_preset_id") or "girl_1"
    preset = next((a for a in AVATAR_PRESETS if a.get("id") == avatar_preset_id), AVATAR_PRESETS[0])
    return {
        "id": persona.get("id"),
        "teacher_id": persona.get("teacher_id"),
        "teacher_name": persona.get("teacher_name"),
        "profession": persona.get("profession"),
        "active_persona_prompt": persona.get("active_persona_prompt"),
        "style_summary": persona.get("style_summary"),
        "avatar_image_url": persona.get("avatar_image_url"),
        "avatar_preset_id": avatar_preset_id,
        "avatar_preset": preset,
        "voice_id": persona.get("voice_id") or preset.get("voice_id"),
        "supported_languages": persona.get("supported_languages") or ["en"],
        "detected_topics": persona.get("detected_topics") or [],
        "updated_at": persona.get("updated_at"),
        "created_at": persona.get("created_at"),
    }


def _video_public(video: dict[str, Any]) -> dict[str, Any]:
    roadmap = roadmaps_repo.first_where(video_id=video["id"]) or {}
    parts = roadmap_parts_repo.where(roadmap_id=roadmap.get("id")) if roadmap else []
    return {
        "id": video.get("id"),
        "title": video.get("title"),
        "description": video.get("description"),
        "subject": video.get("subject"),
        "creator_name": video.get("creator_name"),
        "creator_profession": video.get("creator_profession"),
        "filename": video.get("filename"),
        "thumbnail_url": video.get("thumbnail_url"),
        "duration": video.get("duration"),
        "status": video.get("status"),
        "status_message": video.get("status_message"),
        "has_transcript": bool(video.get("has_transcript")),
        "detected_topics": video.get("detected_topics") or [],
        "parts_count": len(parts),
        "roadmap_id": roadmap.get("id"),
        "created_at": video.get("created_at"),
        "updated_at": video.get("updated_at"),
    }


def _roadmap_full(roadmap: dict[str, Any]) -> dict[str, Any]:
    parts = roadmap_parts_repo.where(roadmap_id=roadmap["id"])
    parts.sort(key=lambda p: p.get("order") or 0)
    return {
        "id": roadmap.get("id"),
        "video_id": roadmap.get("video_id"),
        "persona_id": roadmap.get("persona_id"),
        "title": roadmap.get("title"),
        "summary": roadmap.get("summary"),
        "difficulty": roadmap.get("difficulty"),
        "topics": roadmap.get("topics") or [],
        "parts": [
            {
                "part_id": p.get("part_id"),
                "order": p.get("order"),
                "title": p.get("title"),
                "start_time": p.get("start_time"),
                "end_time": p.get("end_time"),
                "summary": p.get("summary"),
                "concepts": p.get("concepts") or [],
                "equations": p.get("equations") or [],
                "examples": p.get("examples") or [],
                "suggested_visuals": p.get("suggested_visuals") or [],
            }
            for p in parts
        ],
    }


def _stats_for_persona(persona_id: str, teacher_id: str) -> dict[str, Any]:
    videos = videos_repo.where(persona_id=persona_id)
    roadmaps = roadmaps_repo.where(persona_id=persona_id)
    parts_total = sum(len(roadmap_parts_repo.where(roadmap_id=r["id"])) for r in roadmaps)
    sessions = sessions_repo.where(persona_id=persona_id)
    topic_counts: dict[str, int] = {}
    for s in sessions:
        topic = (s.get("selected_topic") or "").strip().lower()
        if topic:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    most_asked = sorted(topic_counts.items(), key=lambda kv: -kv[1])[:5]
    failed_videos = [v for v in videos if v.get("status") == "failed"]
    by_status: dict[str, int] = {}
    for v in videos:
        s = v.get("status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "videos_total": len(videos),
        "videos_by_status": by_status,
        "roadmaps_total": len(roadmaps),
        "roadmap_parts_total": parts_total,
        "sessions_total": len(sessions),
        "most_asked_topics": [{"topic": t, "count": c} for t, c in most_asked],
        "failed_videos": len(failed_videos),
    }


# ---------------------------------------------------------------------------
# HTML pages (require teacher; redirect to /auth/login otherwise)
# ---------------------------------------------------------------------------


@router.get("/teacher/dashboard", response_class=HTMLResponse)
def page_dashboard(request: Request):
    user = _gate_html(request)
    if not user:
        existing = current_user(request)
        if existing and existing.get("role") == "student":
            return RedirectResponse("/student/personas")
        return RedirectResponse("/auth/login")
    return _serve_page("dashboard")


@router.get("/teacher/upload", response_class=HTMLResponse)
def page_upload(request: Request):
    user = _gate_html(request)
    if not user:
        existing = current_user(request)
        if existing and existing.get("role") == "student":
            return RedirectResponse("/student/personas")
        return RedirectResponse("/auth/login")
    return _serve_page("upload")


@router.get("/teacher/videos", response_class=HTMLResponse)
def page_videos(request: Request):
    user = _gate_html(request)
    if not user:
        existing = current_user(request)
        if existing and existing.get("role") == "student":
            return RedirectResponse("/student/personas")
        return RedirectResponse("/auth/login")
    return RedirectResponse("/teacher/dashboard")


@router.get("/teacher/videos/{video_id}", response_class=HTMLResponse)
def page_video_detail(request: Request, video_id: str):  # noqa: ARG001
    user = _gate_html(request)
    if not user:
        existing = current_user(request)
        if existing and existing.get("role") == "student":
            return RedirectResponse("/student/personas")
        return RedirectResponse("/auth/login")
    return _serve_page("video")


@router.get("/teacher/roadmaps", response_class=HTMLResponse)
def page_roadmaps(request: Request):
    user = _gate_html(request)
    if not user:
        existing = current_user(request)
        if existing and existing.get("role") == "student":
            return RedirectResponse("/student/personas")
        return RedirectResponse("/auth/login")
    return _serve_page("roadmaps")


# ---------------------------------------------------------------------------
# Persona API
# ---------------------------------------------------------------------------


@router.get("/api/teacher/persona")
def api_get_persona(user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    return {"persona": _persona_public(persona)}


@router.patch("/api/teacher/persona")
def api_patch_persona(payload: dict[str, Any] = Body(...), user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    fields: dict[str, Any] = {}
    if "profession" in payload:
        fields["profession"] = (payload.get("profession") or "")[:200]
    if "teacher_name" in payload:
        fields["teacher_name"] = (payload.get("teacher_name") or persona.get("teacher_name") or "Teacher")[:120]
    if "style_summary" in payload:
        fields["style_summary"] = (payload.get("style_summary") or "")[:1500]
    if "supported_languages" in payload and isinstance(payload.get("supported_languages"), list):
        fields["supported_languages"] = [str(x) for x in payload["supported_languages"]][:10]

    new_prompt = payload.get("active_persona_prompt")
    if isinstance(new_prompt, str) and new_prompt.strip() and new_prompt.strip() != (persona.get("active_persona_prompt") or "").strip():
        fields["active_persona_prompt"] = new_prompt.strip()
        # Demote previous active version + create a new one tagged manual_edit.
        for v in persona_prompts_repo.where(persona_id=persona["id"]):
            if v.get("is_active"):
                persona_prompts_repo.update(v["id"], {"is_active": False})
        next_version = max(
            (v.get("version") or 0 for v in persona_prompts_repo.where(persona_id=persona["id"])),
            default=0,
        ) + 1
        persona_prompts_repo.create(
            PersonaPromptVersion(
                persona_id=persona["id"],
                version=next_version,
                prompt=new_prompt.strip(),
                reason="manual_edit",
                is_active=True,
            )
        )

    if fields:
        personas_repo.update(persona["id"], fields)
    return {"persona": _persona_public(personas_repo.get(persona["id"]))}


@router.get("/api/teacher/persona/prompts")
def api_persona_prompt_versions(user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    versions = persona_prompts_repo.where(persona_id=persona["id"])
    versions.sort(key=lambda v: v.get("version") or 0, reverse=True)
    return {"versions": versions}


@router.post("/api/teacher/persona/prompts/{version_id}/activate")
def api_activate_prompt(version_id: str, user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    target = persona_prompts_repo.get(version_id)
    if not target or target.get("persona_id") != persona["id"]:
        raise HTTPException(status_code=404, detail="prompt version not found")
    for v in persona_prompts_repo.where(persona_id=persona["id"]):
        persona_prompts_repo.update(v["id"], {"is_active": v["id"] == version_id})
    personas_repo.update(persona["id"], {"active_persona_prompt": target.get("prompt") or ""})
    return {"persona": _persona_public(personas_repo.get(persona["id"]))}


@router.post("/api/teacher/avatar")
def api_set_avatar(payload: dict[str, Any] = Body(...), user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    fields: dict[str, Any] = {}
    avatar_preset_id = (payload.get("avatar_preset_id") or "").strip()
    if avatar_preset_id:
        if not any(a.get("id") == avatar_preset_id for a in AVATAR_PRESETS):
            raise HTTPException(status_code=400, detail="unknown avatar preset")
        fields["avatar_preset_id"] = avatar_preset_id
        preset = next(a for a in AVATAR_PRESETS if a.get("id") == avatar_preset_id)
        if not persona.get("voice_id"):
            fields["voice_id"] = preset.get("voice_id")
    if "avatar_image_url" in payload:
        fields["avatar_image_url"] = (payload.get("avatar_image_url") or "")[:500] or None
    if fields:
        personas_repo.update(persona["id"], fields)
    return {"persona": _persona_public(personas_repo.get(persona["id"]))}


@router.post("/api/teacher/voice")
def api_set_voice(payload: dict[str, Any] = Body(...), user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    voice_id = (payload.get("voice_id") or "").strip()
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id required")
    personas_repo.update(persona["id"], {"voice_id": voice_id[:120]})
    return {"persona": _persona_public(personas_repo.get(persona["id"]))}


# ---------------------------------------------------------------------------
# Video upload + lifecycle
# ---------------------------------------------------------------------------


@router.post("/api/teacher/videos/upload")
async def api_upload_video(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    description: str = Form(default=""),
    subject: str = Form(default=""),
    teacher_name: str = Form(default=""),
    profession: str = Form(default=""),
    user: dict = Depends(require_teacher),
):
    persona = _ensure_persona(user)
    persona_updates: dict[str, Any] = {}
    if teacher_name and teacher_name.strip() and teacher_name.strip() != persona.get("teacher_name"):
        persona_updates["teacher_name"] = teacher_name.strip()[:120]
    if profession and profession.strip() and profession.strip() != persona.get("profession"):
        persona_updates["profession"] = profession.strip()[:200]
    if persona_updates:
        personas_repo.update(persona["id"], persona_updates)
        persona = personas_repo.get(persona["id"])

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    video_id = uuid.uuid4().hex[:8]
    filename = safe_video_filename(video_id, file.filename)
    out_path = UPLOADS_DIR / filename
    with out_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    thumb_path = THUMBNAILS_DIR / f"{video_id}.jpg"
    thumb_ok = extract_thumbnail(out_path, thumb_path)

    payload = TeacherVideo(
        id=video_id,
        teacher_id=user["id"],
        persona_id=persona["id"],
        title=(title or Path(file.filename or "").stem or f"Video {video_id}")[:300],
        description=(description or "")[:5000],
        subject=(subject or "")[:200],
        creator_name=(teacher_name or persona.get("teacher_name") or user.get("name") or "Teacher")[:120],
        creator_profession=(profession or persona.get("profession") or "")[:200],
        filename=filename,
        thumbnail_url=f"/thumbnail/{video_id}" if thumb_ok else None,
        status="uploaded",
        status_message="queued for processing",
    )
    videos_repo.create(payload)
    background.add_task(process_teacher_video_sync, video_id)
    return {"video": _video_public(videos_repo.get(video_id))}


@router.get("/api/teacher/videos")
def api_list_videos(user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    videos = videos_repo.where(persona_id=persona["id"])
    videos.sort(key=lambda v: v.get("created_at") or "", reverse=True)
    return {"videos": [_video_public(v) for v in videos]}


@router.get("/api/teacher/videos/{video_id}")
def api_get_video(video_id: str, user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    video = videos_repo.get(video_id)
    if not video or video.get("persona_id") != persona["id"]:
        raise HTTPException(status_code=404, detail="video not found")
    roadmap = roadmaps_repo.first_where(video_id=video_id)
    return {
        "video": _video_public(video),
        "roadmap": _roadmap_full(roadmap) if roadmap else None,
    }


@router.post("/api/teacher/videos/{video_id}/reprocess")
def api_reprocess(video_id: str, background: BackgroundTasks, user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    video = videos_repo.get(video_id)
    if not video or video.get("persona_id") != persona["id"]:
        raise HTTPException(status_code=404, detail="video not found")
    videos_repo.update(video_id, {"status": "uploaded", "status_message": "reprocess queued"})
    background.add_task(process_teacher_video_sync, video_id)
    return {"video": _video_public(videos_repo.get(video_id))}


@router.delete("/api/teacher/videos/{video_id}")
def api_delete_video(video_id: str, user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    video = videos_repo.get(video_id)
    if not video or video.get("persona_id") != persona["id"]:
        raise HTTPException(status_code=404, detail="video not found")
    # Remove roadmap + parts
    for r in roadmaps_repo.where(video_id=video_id):
        for p in roadmap_parts_repo.where(roadmap_id=r["id"]):
            roadmap_parts_repo.delete(p["id"])
        roadmaps_repo.delete(r["id"])
    # Remove physical assets best-effort
    if video.get("filename"):
        path = UPLOADS_DIR / video["filename"]
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    chunks_dir = DATA_DIR / f"video_{video_id}"
    if chunks_dir.exists():
        try:
            shutil.rmtree(chunks_dir)
        except OSError:
            pass
    thumb = THUMBNAILS_DIR / f"{video_id}.jpg"
    if thumb.exists():
        try:
            thumb.unlink()
        except OSError:
            pass
    videos_repo.delete(video_id)
    return {"ok": True, "video_id": video_id}


@router.get("/api/teacher/roadmaps")
def api_list_roadmaps(user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    roadmaps = roadmaps_repo.where(persona_id=persona["id"])
    out = []
    for r in roadmaps:
        parts_count = len(roadmap_parts_repo.where(roadmap_id=r["id"]))
        video = videos_repo.get(r.get("video_id") or "")
        out.append(
            {
                "id": r.get("id"),
                "video_id": r.get("video_id"),
                "title": r.get("title"),
                "topics": r.get("topics") or [],
                "difficulty": r.get("difficulty"),
                "parts_count": parts_count,
                "video_title": (video or {}).get("title"),
                "video_status": (video or {}).get("status"),
                "thumbnail_url": (video or {}).get("thumbnail_url"),
                "updated_at": r.get("updated_at"),
            }
        )
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"roadmaps": out}


@router.get("/api/teacher/stats")
def api_stats(user: dict = Depends(require_teacher)):
    persona = _ensure_persona(user)
    return {"stats": _stats_for_persona(persona["id"], user["id"]) }


@router.get("/api/teacher/avatars")
def api_avatars(user: dict = Depends(require_teacher)):  # noqa: ARG001
    return {"presets": AVATAR_PRESETS}
