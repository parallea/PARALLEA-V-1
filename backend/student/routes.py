"""Student-facing pages + JSON API.

Pages (HTML, require authenticated student):
  GET /student/personas
  GET /student/learn/{persona_id}    (skeleton; phase 6 owns the immersive UX)

API (JSON, require authenticated student):
  GET  /api/student/personas
  GET  /api/student/personas/{persona_id}
  POST /api/student/sessions                          {persona_id}
  GET  /api/student/sessions/{session_id}
  POST /api/student/sessions/{session_id}/topic       {topic}
  POST /api/student/sessions/{session_id}/message     {content}
  POST /api/student/sessions/{session_id}/part-ended
  GET  /api/student/videos/{video_id}/stream
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from backend.auth.dependencies import current_user, require_student
from backend.services.session_manager import (
    create_session,
    get_session_envelope,
    mark_video_part_ended,
    send_message,
    set_topic,
)
from backend.services.supabase_analytics import track_question_asked, track_session_started
from backend.store import (
    messages_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    sessions_repo,
    users_repo,
    videos_repo,
)
from config import AVATAR_PRESETS, BASE_DIR, PARALLEA_DEFAULT_VOICE_ID, UPLOADS_DIR
from voice import speak_text

logger = logging.getLogger("parallea.student")
router = APIRouter()

STUDENT_PAGES = {
    "personas": BASE_DIR / "student-personas.html",
    "learn": BASE_DIR / "student-learn.html",
}


def _serve_page(slug: str) -> HTMLResponse:
    path = STUDENT_PAGES.get(slug)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail=f"student page '{slug}' missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))


def _client_message_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("message_id") or payload.get("client_message_id") or payload.get("id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _gate_html(request: Request) -> dict | None:
    user = current_user(request)
    if not user:
        return None
    if user.get("role") not in {"student", "admin"}:
        return None
    return user


def _persona_card(persona: dict[str, Any]) -> dict[str, Any]:
    teacher_videos = videos_repo.where(persona_id=persona["id"])
    ready_videos = [v for v in teacher_videos if v.get("status") == "ready"]
    roadmaps = roadmaps_repo.where(persona_id=persona["id"])
    parts_total = sum(len(roadmap_parts_repo.where(roadmap_id=r["id"])) for r in roadmaps)
    avatar_preset = next((a for a in AVATAR_PRESETS if a.get("id") == (persona.get("avatar_preset_id") or "")), AVATAR_PRESETS[0])
    return {
        "id": persona.get("id"),
        "teacher_name": persona.get("teacher_name"),
        "profession": persona.get("profession"),
        "style_summary": persona.get("style_summary"),
        "avatar_image_url": persona.get("avatar_image_url"),
        "avatar_preset_id": persona.get("avatar_preset_id") or avatar_preset.get("id"),
        "avatar_preset": avatar_preset,
        "voice_id": persona.get("voice_id") or avatar_preset.get("voice_id"),
        "supported_languages": persona.get("supported_languages") or ["en"],
        "detected_topics": persona.get("detected_topics") or [],
        "videos_count": len(ready_videos),
        "roadmaps_count": len(roadmaps),
        "topics_count": len(persona.get("detected_topics") or []),
        "parts_total": parts_total,
        "ready": bool(persona.get("active_persona_prompt")) and bool(ready_videos),
    }


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


@router.get("/student/personas", response_class=HTMLResponse)
def page_personas(request: Request):
    if not _gate_html(request):
        existing = current_user(request)
        if existing and existing.get("role") in {"teacher", "admin"}:
            return RedirectResponse("/teacher/dashboard")
        return RedirectResponse("/auth/login")
    return _serve_page("personas")


@router.get("/student/learn/{persona_id}", response_class=HTMLResponse)
def page_learn(request: Request, persona_id: str):  # noqa: ARG001
    if not _gate_html(request):
        existing = current_user(request)
        if existing and existing.get("role") in {"teacher", "admin"}:
            return RedirectResponse("/teacher/dashboard")
        return RedirectResponse("/auth/login")
    return _serve_page("learn")


# ---------------------------------------------------------------------------
# Persona browse
# ---------------------------------------------------------------------------


@router.get("/api/student/personas")
def api_personas(user: dict = Depends(require_student)):  # noqa: ARG001
    rows = personas_repo.all()
    rows = [p for p in rows if p.get("active_persona_prompt") or videos_repo.where(persona_id=p.get("id"))]
    cards = [_persona_card(p) for p in rows]
    # Show ready personas first, then the rest.
    cards.sort(key=lambda c: (-int(c["ready"]), -c.get("topics_count", 0), c.get("teacher_name") or ""))
    return {"personas": cards}


@router.get("/api/student/personas/{persona_id}")
def api_persona_detail(persona_id: str, user: dict = Depends(require_student)):  # noqa: ARG001
    persona = personas_repo.get(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="persona not found")
    card = _persona_card(persona)
    roadmaps = [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "summary": r.get("summary"),
            "topics": r.get("topics") or [],
            "difficulty": r.get("difficulty"),
        }
        for r in roadmaps_repo.where(persona_id=persona_id)
    ]
    return {"persona": card, "roadmaps": roadmaps}


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@router.post("/api/student/sessions")
def api_create_session(payload: dict[str, Any] = Body(...), user: dict = Depends(require_student)):
    persona_id = (payload.get("persona_id") or "").strip()
    persona = personas_repo.get(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="persona not found")
    env = create_session(student=user, persona=persona)
    session_id = ((env.get("session") or {}).get("id") or "").strip()
    track_session_started(user, session_id, persona_id)
    return env


@router.get("/api/student/sessions/{session_id}")
def api_get_session(session_id: str, user: dict = Depends(require_student)):
    session = sessions_repo.get(session_id)
    if not session or session.get("student_id") != user["id"]:
        raise HTTPException(status_code=404, detail="session not found")
    env = get_session_envelope(session_id)
    if not env:
        raise HTTPException(status_code=404, detail="session not found")
    return env


@router.post("/api/student/sessions/{session_id}/topic")
async def api_session_topic(session_id: str, payload: dict[str, Any] = Body(...), user: dict = Depends(require_student)):
    session = sessions_repo.get(session_id)
    if not session or session.get("student_id") != user["id"]:
        raise HTTPException(status_code=404, detail="session not found")
    topic = (payload.get("topic") or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    env = await set_topic(session_id, topic)
    if not env:
        raise HTTPException(status_code=404, detail="session not found")
    return env


@router.post("/api/student/sessions/{session_id}/message")
async def api_session_message(session_id: str, payload: dict[str, Any] = Body(...), user: dict = Depends(require_student)):
    session = sessions_repo.get(session_id)
    if not session or session.get("student_id") != user["id"]:
        raise HTTPException(status_code=404, detail="session not found")
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    env = await send_message(session_id, content)
    if not env:
        raise HTTPException(status_code=404, detail="session not found")
    track_question_asked(
        user,
        session_id,
        content,
        session.get("persona_id"),
        message_id=_client_message_id(payload),
    )
    return env


@router.post("/api/student/sessions/{session_id}/part-ended")
def api_session_part_ended(session_id: str, user: dict = Depends(require_student)):
    session = sessions_repo.get(session_id)
    if not session or session.get("student_id") != user["id"]:
        raise HTTPException(status_code=404, detail="session not found")
    env = mark_video_part_ended(session_id)
    if not env:
        raise HTTPException(status_code=404, detail="session not found")
    return env


@router.get("/api/student/videos/{video_id}/stream")
def api_student_video_stream(video_id: str, user: dict = Depends(require_student)):  # noqa: ARG001
    video = videos_repo.get(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="video not found")
    filename = (video.get("filename") or "").strip()
    if not filename:
        raise HTTPException(status_code=404, detail="video file missing")
    root = UPLOADS_DIR.resolve()
    path = (UPLOADS_DIR / filename).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(status_code=403, detail="invalid video path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="video file missing")
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return FileResponse(path, media_type=media_type)


@router.post("/api/student/sessions/{session_id}/messages/{message_id}/audio")
async def api_message_audio(session_id: str, message_id: str, user: dict = Depends(require_student)):
    session = sessions_repo.get(session_id)
    if not session or session.get("student_id") != user["id"]:
        raise HTTPException(status_code=404, detail="session not found")
    message = messages_repo.get(message_id)
    if not message or message.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="message not found")
    if message.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="only assistant messages have audio")
    persona = personas_repo.get(session.get("persona_id") or "")
    voice_id = (persona or {}).get("voice_id") or PARALLEA_DEFAULT_VOICE_ID
    text = message.get("content") or ""
    try:
        result = await speak_text(session_id=session_id, text=text, voice_id=voice_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"TTS unavailable: {exc}")
    return {"audio_url": result["audio_url"], "voice_id": voice_id}
