from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from blackboard_visuals import build_blackboard_visual_payload
from backend.services.openai_manim_pipeline import openai_pipeline_status
from backend.services.session_state import get_teaching_session_state as ensure_teaching_session_state
from config import (
    AUDIO_DIR,
    AVATAR_PRESETS,
    BASE_DIR,
    DATA_DIR,
    DEFAULT_AVATAR_ID,
    MANIM_PUBLIC_BASE_URL,
    MANIM_PUBLIC_OUTPUT_DIR,
    MAX_HISTORY,
    MAX_NOTES,
    PUBLIC_DIR,
    RENDERS_DIR,
    SESSIONS_DIR,
    TTS_AUDIO_EXTENSION,
    THUMBNAILS_DIR,
    UPLOADS_DIR,
    VIDEOS_DB,
)
from data_indexer import build_index
from manim_renderer import log_manim_runtime_status, manim_runtime_info, render_manim_healthcheck
from teaching_pipeline import (
    build_pipeline_board_actions,
    build_visual_segment_for_frame,
    materialize_frame_plan,
    plan_frame_for_segment,
)
from transcribe import save_chunks, transcribe_with_timestamps
from voice import (
    audio_duration_seconds,
    speak_cached,
    speak_segments,
    speak_text,
    stt_provider_status,
    transcribe_question,
    transcribe_question_result,
    tts_provider_status,
    AudioConversionError,
    AudioInputError,
    VoicePipelineError,
)

# Optional imports from V2 backend
try:
    from rag import (
        get_greeting_async,
        get_lesson_teacher_response_async,
        get_teaching_blueprint_async,
        get_teaching_response_async,
        stream_teaching_blueprint_async,
    )
except Exception:
    get_greeting_async = None
    get_lesson_teacher_response_async = None
    get_teaching_blueprint_async = None
    get_teaching_response_async = None
    stream_teaching_blueprint_async = None

app = FastAPI(title="Parallea V2 Layered")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Auth: Starlette session middleware (used by Google OAuth state) + auth router.
from backend.auth import auth_router  # noqa: E402
from backend.auth.admin import log_admin_auth_status  # noqa: E402
from backend.auth.oauth import register_oauth_state_middleware  # noqa: E402
from backend.admin import admin_router  # noqa: E402
from backend.services.supabase_analytics import log_supabase_analytics_status  # noqa: E402
from backend.services.generated_media_cleanup import cleanup_expired_generated_media, get_active_generated_media  # noqa: E402
from backend.services.storage_service import LOCAL_STORAGE_DIR, log_storage_status, storage_status, url_for_object  # noqa: E402
from backend.store import videos_repo as teacher_videos_repo  # noqa: E402
from backend.teacher import teacher_router  # noqa: E402
from backend.student import student_router  # noqa: E402
register_oauth_state_middleware(app)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(teacher_router)
app.include_router(student_router)

# Run the persona-store seed on boot so legacy `data/videos.json` rows are
# always reflected in the new tables. Safe to call repeatedly.
try:
    from backend.store.migrate import run_migration as _run_persona_migration  # noqa: E402
    _run_persona_migration()
except Exception as _seed_err:  # noqa: BLE001
    logging.getLogger("parallea").warning("persona seed migration skipped: %s", _seed_err)

app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAILS_DIR), check_dir=False), name="thumbnails")

INDEX_HTML = BASE_DIR / "index.html"
PLAYER_HTML = BASE_DIR / "player.html"
AVATAR_SELECT_HTML = BASE_DIR / "avatar-select.html"
LEARN_HTML = BASE_DIR / "learn.html"
DEV_VOICE_TEST_HTML = BASE_DIR / "dev-voice-test.html"
BOARD_ASSETS_DIR = BASE_DIR / "board_assets"

app.mount("/board-assets", StaticFiles(directory=str(BOARD_ASSETS_DIR), check_dir=False), name="board-assets")
if MANIM_PUBLIC_BASE_URL.startswith("/") and MANIM_PUBLIC_BASE_URL != "/rendered-scenes":
    app.mount(
        MANIM_PUBLIC_BASE_URL,
        StaticFiles(directory=str(MANIM_PUBLIC_OUTPUT_DIR), check_dir=False),
        name="rendered-scenes-manim",
    )
elif MANIM_PUBLIC_BASE_URL and not MANIM_PUBLIC_BASE_URL.startswith("/"):
    logger = logging.getLogger("parallea")
    logger.warning("MANIM_PUBLIC_BASE_URL is not a local mount path; skipping StaticFiles mount value=%s", MANIM_PUBLIC_BASE_URL)
app.mount("/rendered-scenes", StaticFiles(directory=str(RENDERS_DIR), check_dir=False), name="rendered-scenes")
app.mount("/generated", StaticFiles(directory=str(PUBLIC_DIR / "generated"), check_dir=False), name="generated")
app.mount("/storage", StaticFiles(directory=str(LOCAL_STORAGE_DIR), check_dir=False), name="storage")

_sessions: Dict[str, Dict[str, Any]] = {}
_audio_jobs: Dict[str, Dict[str, Any]] = {}
logger = logging.getLogger("parallea")
_UNSERIALIZABLE = object()
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "briefly", "by", "can", "could", "did",
    "do", "does", "for", "from", "get", "give", "go", "help", "how", "i", "if", "in", "into", "is",
    "it", "its", "let", "like", "me", "more", "my", "of", "on", "or", "our", "please", "re", "show",
    "so", "step", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "through", "to", "up", "use", "used", "video", "walk", "what", "when", "where", "which",
    "why", "with", "would", "you", "your",
}
SIGNAL_PROMPTS = {
    "slow_down": "Slow down and explain the current idea in simpler language.",
    "rewind": "Replay the last important source moment and explain it again.",
    "example": "Give me a concrete example for the current concept.",
    "next": "Move to the next important idea in the lesson.",
}
AVATAR_AUDIO_VERSION = "v3"


def session_file_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in clean_spaces(sid))
    return SESSIONS_DIR / f"{safe_sid or 'session'}.json"


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            safe_value = make_json_safe(item)
            if safe_value is not _UNSERIALIZABLE:
                cleaned[str(key)] = safe_value
        return cleaned
    if isinstance(value, (list, tuple)):
        cleaned_items = []
        for item in value:
            safe_value = make_json_safe(item)
            if safe_value is not _UNSERIALIZABLE:
                cleaned_items.append(safe_value)
        return cleaned_items
    return _UNSERIALIZABLE


def persist_session(sid: str) -> None:
    sess = _sessions.get(sid)
    if not isinstance(sess, dict):
        return
    safe_session = make_json_safe(sess) or {}
    session_file_path(sid).write_text(json.dumps(safe_session, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_session_data(sess: dict | None) -> None:
    if not isinstance(sess, dict):
        return
    sid = clean_spaces(sess.get("id"))
    if sid:
        persist_session(sid)


def load_session(sid: str) -> dict | None:
    if sid in _sessions:
        return _sessions[sid]
    path = session_file_path(sid)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to load session sid=%s", sid)
        return None
    if not isinstance(data, dict):
        return None
    data["id"] = clean_spaces(data.get("id")) or sid
    _sessions[sid] = data
    return data


def load_persisted_sessions() -> None:
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to parse persisted session file=%s", path)
            continue
        if not isinstance(data, dict):
            continue
        sid = clean_spaces(data.get("id")) or path.stem
        data["id"] = sid
        _sessions[sid] = data


def remove_session(sid: str) -> None:
    _sessions.pop(sid, None)
    try:
        session_file_path(sid).unlink(missing_ok=True)
    except Exception:
        logger.exception("failed to delete persisted session sid=%s", sid)


def create_audio_job(job_id: str, segment: dict[str, Any]) -> None:
    _audio_jobs[job_id] = {
        "job_id": job_id,
        "segment_id": clean_spaces(segment.get("segment_id")),
        "ready": False,
        "url": None,
        "error": None,
    }


async def run_audio_job(
    job_id: str,
    *,
    text: str,
    voice_id: str,
    lang: str,
    fallback_voice: str | None = None,
) -> None:
    job = _audio_jobs.setdefault(job_id, {"job_id": job_id, "ready": False, "url": None, "error": None})
    try:
        audio = await speak_text(
            session_id="jobs",
            text=text,
            voice_id=voice_id,
            lang=lang,
            fallback_voice=fallback_voice,
            message_id=job_id,
        )
        job["ready"] = True
        job["url"] = audio.get("audio_url")
        job["error"] = None
    except Exception as exc:
        job["ready"] = False
        job["error"] = str(exc)
        logger.exception("audio job failed job_id=%s error=%s", job_id, exc)


def schedule_background_task(background_tasks: BackgroundTasks, func: Any, *args: Any, **kwargs: Any) -> None:
    background_tasks.add_task(func, *args, **kwargs)
    task = background_tasks.tasks[-1]
    asyncio.create_task(task())


def queue_segment_audio_job(
    background_tasks: BackgroundTasks,
    *,
    session_id: str,
    segment: dict[str, Any],
    voice_id: str,
    lang: str,
    fallback_voice: str | None = None,
) -> str | None:
    text = clean_spaces(segment.get("speech_text") or segment.get("text"))
    if not text:
        return None
    job_id = f"{clean_spaces(session_id) or 'job'}_{uuid.uuid4().hex}"
    create_audio_job(job_id, segment)
    schedule_background_task(
        background_tasks,
        run_audio_job,
        job_id,
        text=text,
        voice_id=voice_id,
        lang=lang,
        fallback_voice=fallback_voice,
    )
    return job_id

def ensure_dirs():
    for d in [DATA_DIR, UPLOADS_DIR, THUMBNAILS_DIR, AUDIO_DIR, SESSIONS_DIR, BOARD_ASSETS_DIR, RENDERS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def normalize_chunks_path_value(raw_path: Any) -> str:
    text = clean_spaces(raw_path)
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()
        except Exception:
            try:
                return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
            except Exception:
                return text.replace("\\", "/")
    portable = text.replace("\\", "/").lstrip("./")
    if portable.startswith("data/"):
        portable = portable[len("data/"):]
    return portable


def resolve_chunks_path(raw_path: Any) -> Path | None:
    normalized = normalize_chunks_path_value(raw_path)
    if not normalized:
        return None
    path = Path(normalized)
    if path.is_absolute():
        return path
    data_candidate = DATA_DIR / path
    if data_candidate.exists() or not normalized.startswith(("board_assets/", "uploads/", "thumbnails/")):
        return data_candidate
    return BASE_DIR / path


def normalize_video_record(item: dict) -> dict | None:
    normalized = dict(item)
    normalized["id"] = clean_spaces(normalized.get("id"))
    normalized["title"] = clean_spaces(normalized.get("title")) or "Untitled lesson"
    normalized["creator_name"] = clean_spaces(normalized.get("creator_name") or normalized.get("creator")) or "Creator"
    normalized["creator_profession"] = clean_spaces(normalized.get("creator_profession")) or "Educator"
    normalized["creator"] = normalized["creator_name"]
    filename = clean_spaces(normalized.get("filename"))
    normalized["filename"] = Path(filename.replace("\\", "/")).name if filename else ""
    raw_chunks_path = normalized.get("chunks_path")
    if raw_chunks_path:
        normalized["chunks_path"] = normalize_chunks_path_value(raw_chunks_path)
    else:
        normalized["chunks_path"] = None

    if not normalized["id"] or not normalized["filename"]:
        return None

    video_path = UPLOADS_DIR / normalized["filename"]
    if not video_path.exists():
        return None

    thumb_path = THUMBNAILS_DIR / f"{normalized['id']}.jpg"
    normalized["thumbnail_url"] = f"/thumbnail/{normalized['id']}" if thumb_path.exists() else None

    chunks_path = resolve_chunks_path(normalized.get("chunks_path"))
    if chunks_path and chunks_path.exists():
        normalized["chunks_path"] = normalize_chunks_path_value(chunks_path)
        normalized["has_transcript"] = True
    else:
        normalized["chunks_path"] = None
        normalized["has_transcript"] = False
    return normalized

def load_videos() -> list[dict]:
    ensure_dirs()
    if not VIDEOS_DB.exists():
        return []
    try:
        items = json.loads(VIDEOS_DB.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    normalized_items = [normalized for item in items if isinstance(item, dict) for normalized in [normalize_video_record(item)] if normalized]
    if normalized_items != items:
        save_videos(normalized_items)
    return normalized_items

def save_videos(items: list[dict]) -> None:
    ensure_dirs()
    VIDEOS_DB.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

def find_video(video_id: str) -> dict | None:
    return next((v for v in load_videos() if v["id"] == video_id), None)

def remove_video_from_index(video_id: str) -> dict | None:
    items = load_videos()
    target = next((item for item in items if item["id"] == video_id), None)
    if not target:
        return None
    save_videos([item for item in items if item["id"] != video_id])
    return target

def avatar_by_id(avatar_id: str) -> dict:
    return next((a for a in AVATAR_PRESETS if a["id"] == avatar_id), AVATAR_PRESETS[0])

def avatar_voice_id(avatar: dict) -> str:
    return (avatar.get("voice_id") or "").strip()

def tts_diagnostics() -> dict:
    provider = tts_provider_status()
    missing = []
    if not provider["available"]:
        missing.append("edge-tts")
    return {
        "provider": provider["provider"],
        "configured": not missing,
        "missing": missing,
        "avatar_voice_ids": {avatar["id"]: bool(avatar_voice_id(avatar)) for avatar in AVATAR_PRESETS},
        "default_voice": provider["default_voice"],
        "audio_extension": provider["audio_extension"],
    }

def cached_avatar_audio_name(kind: str, avatar: dict) -> str:
    return f"{kind}_{AVATAR_AUDIO_VERSION}_{avatar['id']}{TTS_AUDIO_EXTENSION}"

def cached_avatar_audio_url(kind: str, avatar: dict) -> str | None:
    filename = cached_avatar_audio_name(kind, avatar)
    path = AUDIO_DIR / filename
    if path.exists() and path.stat().st_size > 0:
        return f"/audio-response/{filename}"
    safe_key = filename.rsplit(".", 1)[0]
    cached = get_active_generated_media("cached", safe_key, "audio")
    if cached and cached.get("url"):
        return str(cached["url"])
    return None

def remove_cached_lesson_audio(video_id: str) -> list[str]:
    removed = []
    for path in AUDIO_DIR.glob(f"lesson_greeting_{AVATAR_AUDIO_VERSION}_{video_id}_*"):
        try:
            path.unlink(missing_ok=True)
            removed.append(path.name)
        except Exception as exc:
            print(f"Could not delete cached lesson audio {path.name}: {exc}")
    return removed

def delete_video_storage(video: dict) -> dict:
    removed = {"video_file": None, "thumbnail": None, "data_dir": None, "cached_audio": []}

    filename = video.get("filename")
    if filename:
        video_path = UPLOADS_DIR / filename
        if video_path.exists():
            video_path.unlink(missing_ok=True)
            removed["video_file"] = str(video_path)

    video_id = str(video.get("id") or "")
    thumb_path = THUMBNAILS_DIR / f"{video_id}.jpg"
    if thumb_path.exists():
        thumb_path.unlink(missing_ok=True)
        removed["thumbnail"] = str(thumb_path)

    raw_chunks_path = video.get("chunks_path")
    if raw_chunks_path:
        chunks_path = resolve_chunks_path(raw_chunks_path)
        data_dir = chunks_path.parent if chunks_path else None
        if data_dir and data_dir.exists() and data_dir.is_dir():
            shutil.rmtree(data_dir, ignore_errors=True)
            removed["data_dir"] = str(data_dir)

    removed["cached_audio"] = remove_cached_lesson_audio(video_id)
    return removed

def avatar_preview_text(avatar: dict) -> str:
    return f"Hi, I am {avatar['name']}. I will guide this lesson with vivid visuals, source clips, and spoken conversation."

def fast_greeting_text(avatar: dict) -> str:
    return f"Hi, I am {avatar['name']}. Let us jump into the lesson."

async def ensure_avatar_audio(kind: str, avatar: dict) -> dict:
    line = avatar_preview_text(avatar) if kind == "avatar_preview" else fast_greeting_text(avatar)
    try:
        return await speak_cached(
            cache_key=cached_avatar_audio_name(kind, avatar).rsplit(".", 1)[0],
            text=line,
            voice_id=avatar_voice_id(avatar),
            lang=avatar.get("lang", "en-us"),
            fallback_voice=avatar.get("edge_voice"),
        )
    except Exception as exc:
        print(f"Cached avatar audio failed ({kind}, {avatar['id']}): {exc}")
        existing_url = cached_avatar_audio_url(kind, avatar)
        if existing_url:
            return {"filename": cached_avatar_audio_name(kind, avatar), "audio_url": existing_url}
        return None

async def warm_avatar_audio_cache() -> list[dict]:
    warmed = []
    for avatar in AVATAR_PRESETS:
        sample = await ensure_avatar_audio("avatar_preview", avatar)
        warmed.append(
            {
                "avatar_id": avatar["id"],
                "sample_audio_url": sample.get("audio_url") if sample else None,
            }
        )
    return warmed

def get_session(sid: str) -> dict:
    sess = load_session(sid)
    avatar = avatar_by_id(DEFAULT_AVATAR_ID if sess is None else (sess.get("avatar_id") or DEFAULT_AVATAR_ID))
    if sess is None:
        _sessions[sid] = {
            "id": sid,
            "created": datetime.utcnow().isoformat(),
            "last_active": datetime.utcnow().isoformat(),
            "avatar_id": avatar["id"],
            "voice_id": avatar_voice_id(avatar),
            "voice_label": avatar.get("voice", ""),
            "voice_lang": avatar.get("lang", "en-us"),
            "edge_voice": avatar.get("edge_voice"),
            "history": [],
            "notes": [],
            "greeted": False,
            "transcript_log": [],
            "focus_clip": None,
            "focus_video_id": None,
            "outline_cursor": 0,
            "use_video_context": True,
            "preferred_visualization": "",
            "teaching_loop": {
                "pending_action": None,
                "base_question": "",
                "last_mode": "",
                "source_mode": "",
                "use_video_context": True,
                "preferred_visualization": "",
            },
            "teaching_session_state": ensure_teaching_session_state(None),
        }
        sess = _sessions[sid]
    else:
        sess.setdefault("created", datetime.utcnow().isoformat())
        sess.setdefault("avatar_id", avatar["id"])
        sess.setdefault("voice_id", avatar_voice_id(avatar))
        sess.setdefault("voice_label", avatar.get("voice", ""))
        sess.setdefault("voice_lang", avatar.get("lang", "en-us"))
        sess.setdefault("edge_voice", avatar.get("edge_voice"))
        sess.setdefault("history", [])
        sess.setdefault("notes", [])
        sess.setdefault("greeted", False)
        sess.setdefault("transcript_log", [])
        sess.setdefault("focus_clip", None)
        sess.setdefault("focus_video_id", None)
        sess.setdefault("outline_cursor", 0)
        sess.setdefault("use_video_context", True)
        sess.setdefault("preferred_visualization", "")
        sess.setdefault(
            "teaching_loop",
            {
                "pending_action": None,
                "base_question": "",
                "last_mode": "",
                "source_mode": "",
                "use_video_context": True,
                "preferred_visualization": "",
            },
        )
        sess["teaching_session_state"] = ensure_teaching_session_state(sess)
    _sessions[sid]["last_active"] = datetime.utcnow().isoformat()
    persist_session(sid)
    return _sessions[sid]

def append_history(sid: str, role: str, text: str):
    sess = get_session(sid)
    sess["history"].append({"role": role, "content": text})
    sess["transcript_log"].append({"role": role, "text": text, "ts": datetime.utcnow().isoformat()})
    if len(sess["history"]) > MAX_HISTORY:
        sess["history"] = sess["history"][-MAX_HISTORY:]
    if len(sess["transcript_log"]) > 200:
        sess["transcript_log"] = sess["transcript_log"][-200:]
    persist_session(sid)

def cleanup_sessions():
    cutoff = datetime.utcnow() - timedelta(hours=2)
    stale = []
    for sid, sess in _sessions.items():
        try:
            seen = datetime.fromisoformat(sess["last_active"])
            if seen < cutoff:
                stale.append(sid)
        except Exception:
            stale.append(sid)
    for sid in stale:
        remove_session(sid)

def resolve_html(path: Path) -> str:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Missing file: {path.name}")
    return path.read_text(encoding="utf-8")

def short_log_text(value: Any, limit: int = 120) -> str:
    text = clean_spaces(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

async def parse_request_payload(request: Request) -> Dict[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return {key: value for key, value in form.items()}
    return {}

def extract_thumbnail(video_path: Path, output_path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(video_path), "-ss", "00:00:02", "-vframes", "1", "-q:v", "2", str(output_path)],
            capture_output=True,
            timeout=45,
        )
        return result.returncode == 0 and output_path.exists()
    except Exception:
        return False

def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()

def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2 and token not in STOPWORDS]

def trim_sentence(text: str, limit: int = 140) -> str:
    text = clean_spaces(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip()
    return (cut or text[:limit]).rstrip(".,;: ") + "..."

def sentence_case(text: str) -> str:
    text = clean_spaces(text)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = clean_spaces(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_visualization_preference(value: Any, default: str = "") -> str:
    mode = clean_spaces(value).lower()
    if mode in {"manim", "excalidraw"}:
        return mode
    fallback = clean_spaces(default).lower()
    return fallback if fallback in {"manim", "excalidraw"} else ""


PEDAGOGY_DETAIL_TERMS = {"more", "detail", "deeper", "expand", "elaborate", "more detail", "go deeper"}
PEDAGOGY_CONFUSED_TERMS = {
    "confused", "unclear", "lost", "not clear", "still confused", "still unclear", "i don't get it", "dont get it",
    "i do not get it", "i'm lost", "im lost", "what do you mean",
}
PEDAGOGY_UNDERSTOOD_TERMS = {
    "i understand", "understood", "got it", "that makes sense", "makes sense", "clear now", "i get it", "okay i get it",
}
PEDAGOGY_ADVANCE_TERMS = {
    "next", "move on", "keep going", "continue", "go ahead", "next part", "advance", "move ahead",
}
PEDAGOGY_YES_TERMS = {"yes", "yeah", "yep", "sure", "ok", "okay", "please"}
PEDAGOGY_NO_TERMS = {"no", "nope", "not really"}


def count_meaningful_tokens(text: str) -> int:
    return len([token for token in re.findall(r"[a-z0-9]+", clean_spaces(text).lower()) if token and token not in STOPWORDS])


def matches_any_term(text: str, terms: set[str]) -> bool:
    value = clean_spaces(text).lower()
    return any(term in value for term in terms)


def normalize_teaching_loop(sess: dict) -> dict:
    loop = sess.get("teaching_loop")
    if not isinstance(loop, dict):
        loop = {}
    normalized = {
        "pending_action": clean_spaces(loop.get("pending_action")) or None,
        "base_question": clean_spaces(loop.get("base_question")),
        "last_mode": clean_spaces(loop.get("last_mode")),
        "source_mode": clean_spaces(loop.get("source_mode")),
        "use_video_context": bool(loop.get("use_video_context", sess.get("use_video_context", True))),
        "preferred_visualization": normalize_visualization_preference(
            loop.get("preferred_visualization"),
            sess.get("preferred_visualization", ""),
        ),
    }
    sess["teaching_loop"] = normalized
    persist_session_data(sess)
    return normalized


def next_pending_action_for_mode(mode: str) -> str | None:
    mode_name = clean_spaces(mode).lower()
    if mode_name == "confirm_advance":
        return "awaiting_advance_confirmation"
    if mode_name in {"simple", "detailed", "clarify", "advance"}:
        return "awaiting_detail_or_understanding"
    return None


def teaching_follow_up_mode(question: str, loop: dict | None) -> str | None:
    if not isinstance(loop, dict):
        return None
    pending = clean_spaces(loop.get("pending_action")).lower()
    if not pending:
        return None
    lower = clean_spaces(question).lower()
    if not lower:
        return None
    substantive = count_meaningful_tokens(lower)
    if substantive >= 5 and not any(matches_any_term(lower, terms) for terms in [
        PEDAGOGY_DETAIL_TERMS,
        PEDAGOGY_CONFUSED_TERMS,
        PEDAGOGY_UNDERSTOOD_TERMS,
        PEDAGOGY_ADVANCE_TERMS,
    ]):
        return None
    if pending == "awaiting_advance_confirmation":
        if matches_any_term(lower, PEDAGOGY_ADVANCE_TERMS) or lower in PEDAGOGY_YES_TERMS:
            return "advance"
        if matches_any_term(lower, PEDAGOGY_CONFUSED_TERMS):
            return "clarify"
        if matches_any_term(lower, PEDAGOGY_DETAIL_TERMS):
            return "detailed"
        return None
    if matches_any_term(lower, PEDAGOGY_CONFUSED_TERMS):
        return "clarify"
    if matches_any_term(lower, PEDAGOGY_DETAIL_TERMS) or lower in PEDAGOGY_YES_TERMS:
        return "detailed"
    if matches_any_term(lower, PEDAGOGY_UNDERSTOOD_TERMS) or lower in PEDAGOGY_NO_TERMS:
        return "confirm_advance"
    if matches_any_term(lower, PEDAGOGY_ADVANCE_TERMS):
        return "advance"
    return None


def effective_question_for_mode(base_question: str, learner_request: str, pedagogy_mode: str) -> str:
    base = clean_spaces(base_question) or clean_spaces(learner_request)
    request = clean_spaces(learner_request)
    mode_name = clean_spaces(pedagogy_mode).lower()
    if mode_name == "detailed":
        return f"Give a deeper explanation of {base}."
    if mode_name == "clarify":
        return f"Explain {base} again in a clearer and simpler way."
    if mode_name == "confirm_advance":
        return f"The learner says they understand {base}. Briefly acknowledge that and ask whether they want to move to the next part."
    if mode_name == "advance":
        return f"Continue to the next meaningful teaching step for {base}."
    return request or base


def teaching_request_context(
    question: str,
    sess: dict,
    *,
    source_mode: str,
    use_video_context: bool = True,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    loop = normalize_teaching_loop(sess)
    follow_up_mode = teaching_follow_up_mode(question, loop) if loop.get("source_mode") == source_mode else None
    base_question = loop.get("base_question") if follow_up_mode else clean_spaces(question)
    if not base_question:
        base_question = clean_spaces(question)
    pedagogy_mode = follow_up_mode or "simple"
    learner_request = clean_spaces(question)
    return {
        "pedagogy_mode": pedagogy_mode,
        "learner_request": learner_request,
        "base_question": base_question,
        "effective_question": effective_question_for_mode(base_question, learner_request, pedagogy_mode),
        "use_video_context": use_video_context,
        "source_mode": source_mode,
        "preferred_visualization": normalize_visualization_preference(
            preferred_visualization,
            loop.get("preferred_visualization") or sess.get("preferred_visualization", ""),
        ),
    }


def remember_teaching_loop(sess: dict, request_ctx: dict[str, Any]) -> None:
    sess["use_video_context"] = bool(request_ctx.get("use_video_context", True))
    sess["preferred_visualization"] = normalize_visualization_preference(
        request_ctx.get("preferred_visualization"),
        sess.get("preferred_visualization", ""),
    )
    sess["teaching_loop"] = {
        "pending_action": next_pending_action_for_mode(request_ctx.get("pedagogy_mode", "")),
        "base_question": clean_spaces(request_ctx.get("base_question")),
        "last_mode": clean_spaces(request_ctx.get("pedagogy_mode")),
        "source_mode": clean_spaces(request_ctx.get("source_mode")),
        "use_video_context": bool(request_ctx.get("use_video_context", True)),
        "preferred_visualization": sess["preferred_visualization"],
    }
    persist_session_data(sess)

def load_video_chunks(video: dict | None) -> list[dict]:
    if not video:
        return []
    raw_path = video.get("chunks_path")
    if not raw_path:
        return []
    path = resolve_chunks_path(raw_path)
    if path is None:
        return []
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    chunks = []
    for key in sorted(data.keys(), key=lambda item: int(item)):
        item = data[key]
        chunks.append(
            {
                "index": int(key),
                "start_sec": float(item.get("start_sec", 0.0)),
                "end_sec": float(item.get("end_sec", 0.0)),
                "text": clean_spaces(item.get("text", "")),
            }
        )
    return chunks

def choose_outline(chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []
    indexes = sorted({0, len(chunks) // 3, (2 * len(chunks)) // 3, len(chunks) - 1})
    outline = []
    for idx in indexes:
        chunk = chunks[idx]
        outline.append(
            {
                "title": trim_sentence(chunk["text"], 78),
                "start_sec": chunk["start_sec"],
                "end_sec": chunk["end_sec"],
            }
        )
    return outline

def lesson_context_lines(chunks: list[dict], limit: int = 2) -> list[str]:
    source = choose_outline(chunks)
    if not source:
        source = [{"title": chunk.get("text", "")} for chunk in chunks[:limit]]
    lines = []
    for item in source:
        text = sentence_case(trim_sentence(item.get("title") or item.get("text") or "", 110))
        if text and text not in lines:
            lines.append(text)
        if len(lines) >= limit:
            break
    return lines

def lesson_context_text(chunks: list[dict], limit: int = 2) -> str:
    return "\n".join(lesson_context_lines(chunks, limit=limit))

LESSON_TUTOR_SUGGESTIONS = [
    "Explain this section",
    "Give me a practice task",
    "Test me",
    "Summarize this part",
]

LESSON_TUTOR_SYSTEM_PROMPT = (
    "You are a warm, skilled teacher helping a learner progress through a structured lesson. "
    "Your role is to guide the learner through the current lesson section step by step. "
    "Always prioritize the current lesson context over general conversation. "
    "Explain things simply, clearly, and encouragingly. "
    "Use beginner-friendly teaching language. "
    "When useful, give practical exercises tied to the current lesson context. "
    "Keep responses conversational and not too long. "
    "If the learner is confused, explain in a different way. "
    "If the learner asks something outside the lesson, answer briefly and guide them back to the current section. "
    "You are not a generic chatbot. You are an in-lesson AI tutor."
)

def coerce_text_list(value: Any, limit: int = 12, item_limit: int = 180) -> list[str]:
    items: list[Any]
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            items = parsed if isinstance(parsed, list) else [raw]
        else:
            parts = [part for part in re.split(r"\n+|\s\|\s", raw) if clean_spaces(part)]
            items = parts or [raw]
    else:
        return []
    cleaned = []
    for item in items[:limit]:
        text = clean_spaces(item)
        if text:
            cleaned.append(trim_sentence(text, item_limit))
    return cleaned

def coerce_float_list(value: Any, limit: int = 12) -> list[float]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                items = json.loads(raw)
            except Exception:
                items = [raw]
        else:
            items = [item for item in re.split(r"[\s,|]+", raw) if item]
    else:
        return []
    values = []
    for item in items[:limit]:
        try:
            values.append(float(item))
        except Exception:
            continue
    return values

def parse_lesson_context(payload: Dict[str, Any]) -> dict:
    section_index = payload.get("section_index")
    total_sections = payload.get("total_sections")
    try:
        section_index = int(section_index)
    except Exception:
        section_index = 0
    try:
        total_sections = int(total_sections)
    except Exception:
        total_sections = 1
    total_sections = max(1, total_sections)
    section_index = max(0, min(section_index, total_sections - 1))
    context = {
        "lesson_id": clean_spaces(payload.get("lesson_id")),
        "lesson_title": clean_spaces(payload.get("lesson_title") or "Lesson"),
        "lesson_description": trim_sentence(clean_spaces(payload.get("lesson_description")), 220),
        "current_section_id": clean_spaces(payload.get("current_section_id") or f"section_{section_index + 1}"),
        "current_section_title": clean_spaces(payload.get("current_section_title") or f"Section {section_index + 1}"),
        "current_section_content": trim_sentence(clean_spaces(payload.get("current_section_content")), 320),
        "section_index": section_index,
        "total_sections": total_sections,
        "section_order": coerce_text_list(payload.get("section_order"), limit=12, item_limit=72),
        "visible_metadata": coerce_text_list(payload.get("visible_metadata"), limit=12, item_limit=140),
        "timestamps": coerce_float_list(payload.get("timestamps"), limit=8),
    }
    return context

def lesson_context_blob(context: dict) -> str:
    lines = [
        context.get("lesson_title", ""),
        context.get("lesson_description", ""),
        context.get("current_section_title", ""),
        context.get("current_section_content", ""),
    ]
    lines.extend(context.get("visible_metadata", []))
    return " ".join(item for item in lines if item)

def lesson_overlap_score(question: str, context: dict) -> int:
    q_tokens = set(tokenize(question))
    if not q_tokens:
        return 0
    context_tokens = set(tokenize(lesson_context_blob(context)))
    return len(q_tokens & context_tokens)

def lesson_metadata_lines(context: dict, limit: int = 3) -> list[str]:
    lines = []
    for item in context.get("visible_metadata", []):
        text = sentence_case(trim_sentence(item, 120))
        if text and text not in lines:
            lines.append(text)
        if len(lines) >= limit:
            break
    return lines

def lesson_practice_line(context: dict) -> str:
    for item in context.get("visible_metadata", []):
        lower = item.lower()
        if any(term in lower for term in ["practice", "repeat", "strum", "finger", "listen", "tune", "play", "focus", "peg", "switch"]):
            return sentence_case(trim_sentence(item, 120))
    content = context.get("current_section_content") or ""
    if content:
        return sentence_case(trim_sentence(content, 120))
    return "Take it slowly and make each move clean before you speed up."

def lesson_test_prompt(context: dict) -> str:
    title = context.get("current_section_title", "").lower()
    visible_items = context.get("visible_metadata", [])
    if "tune" in title:
        note = "the target note"
        for item in visible_items:
            match = re.search(r"target note:\s*([A-Ga-g][#b]?\d?)", item, flags=re.IGNORECASE)
            if match:
                note = match.group(1)
                break
        return f"Quick check. What note should the current string reach before you move on? The answer is {note}. If you can name it and center the tuner, you are ready."
    if "chord" in title:
        chord = "the current chord"
        for item in visible_items:
            match = re.search(r"current chord:\s*([A-G][^,]*)", item, flags=re.IGNORECASE)
            if match:
                chord = clean_spaces(match.group(1))
                break
        return f"Quick check. Can you say which fingers belong in {chord} and which string you start strumming from? If you can explain that out loud, your hand map is getting stronger."
    if "part" in title:
        return "Quick check. Point to the part on the screen, say its name out loud, and say what it does in one simple sentence. If you can do that without guessing, the section is sticking."
    return "Quick check. Tell me the key idea of this section in one sentence, then compare it with the details on the screen."

def lesson_summary_line(context: dict) -> str:
    summary = context.get("current_section_content")
    if summary:
        return sentence_case(trim_sentence(summary, 170))
    meta = lesson_metadata_lines(context, limit=2)
    if meta:
        return " ".join(meta[:2])
    return sentence_case(f"This part of {context.get('lesson_title', 'the lesson')} is about {context.get('current_section_title', 'the current section')}.")

def build_lesson_greeting_payload(context: dict) -> dict:
    section = context.get("current_section_title", "this section")
    lesson_title = context.get("lesson_title", "this lesson")
    summary = lesson_summary_line(context)
    greeting = (
        f"Welcome to {lesson_title}. We are starting with {section}. "
        f"{summary} "
        "Stay with one small step at a time, and ask me whenever you want this part explained more simply."
    )
    return {
        "greeting": greeting,
        "text": greeting,
        "suggestions": LESSON_TUTOR_SUGGESTIONS,
        "source": "lesson",
        "has_audio": False,
        "audio_url": None,
    }

def build_lesson_chat_payload(question: str, context: dict, trigger: str | None = None) -> dict:
    q = clean_spaces(question)
    lower = q.lower()
    trigger_name = clean_spaces(trigger).lower() or ""
    section = context.get("current_section_title", "this part")
    lesson_title = context.get("lesson_title", "this lesson")
    summary = lesson_summary_line(context)
    details = lesson_metadata_lines(context, limit=3)
    detail_line = " ".join(details[:2]).strip()
    practice_line = lesson_practice_line(context)
    overlap = lesson_overlap_score(q, context)

    if trigger_name == "section_change":
        answer = (
            f"We just moved into {section}. "
            f"{summary} "
            f"{detail_line or practice_line} "
            "Take this part slowly, and let your hands learn one clean motion before you add speed."
        )
        follow_up = "Do you want a practice task, a short test, or a simpler explanation for this section?"
    elif trigger_name == "practice" or any(term in lower for term in ["practice", "exercise", "task", "drill"]):
        answer = (
            f"Try this practice loop for {section}. "
            f"{practice_line} "
            "Do five slow repetitions, pause, then do five more while listening for cleaner timing and cleaner note shape."
        )
        follow_up = "Want me to make that easier, harder, or more rhythmic?"
    elif trigger_name == "test" or any(term in lower for term in ["test me", "quiz", "check me"]):
        answer = lesson_test_prompt(context)
        follow_up = "If you want, answer me in your own words and I will check it like a teacher."
    elif trigger_name == "summarize" or any(term in lower for term in ["summary", "summarize", "recap"]):
        answer = (
            f"Short version. In {section}, the main thing to hold onto is this: "
            f"{summary} "
            f"{detail_line or 'Keep the movement clean and beginner-simple.'}"
        )
        follow_up = "Do you want the one-line version, or should I turn it into a practice checklist?"
    elif trigger_name == "explain" or any(term in lower for term in ["explain", "what", "how", "why", "show"]):
        answer = (
            f"Here is this section in simple terms. {summary} "
            f"{detail_line or practice_line} "
            "If anything still feels fuzzy, I can say the same idea in a slower and more physical way."
        )
        follow_up = "Should I explain the hand movement, the sound you should hear, or the reason this part matters?"
    elif overlap <= 1:
        answer = (
            f"I can answer that briefly, but I want to keep you anchored to {section}. "
            f"{summary} "
            f"{practice_line} "
            f"For this lesson, let that be the thing you focus on right now."
        )
        follow_up = f"Do you want me to bring that back to {section}, or give you a quick practice cue for it?"
    else:
        answer = (
            f"For {section}, here is the clean idea. {summary} "
            f"{detail_line or practice_line} "
            "Keep it relaxed, keep it clean, and do not rush the move before the shape feels stable."
        )
        follow_up = "Do you want a clearer example, a practice task, or a quick self-check?"

    suggestions = LESSON_TUTOR_SUGGESTIONS[:]
    if trigger_name == "test":
        suggestions = ["Explain this section", "Give me a practice task", "Summarize this part", "Give me another test"]

    return {
        "answer": answer,
        "follow_up": follow_up,
        "suggestions": suggestions,
        "source": "lesson",
        "timestamp": None,
        "timestamps": context.get("timestamps", []),
        "board_actions": [],
        "visual_payload": {"segments": []},
        "reference_bridge": None,
        "context_label": f"{lesson_title} | {section}",
        "system_prompt": LESSON_TUTOR_SYSTEM_PROMPT,
    }

def clip_score(question: str, tokens: list[str], chunk: dict, session: dict) -> float:
    lower = chunk.get("text", "").lower()
    score = 0.0
    for token in tokens:
        if re.search(rf"\b{re.escape(token)}\b", lower):
            score += 5.0
        elif token in lower:
            score += 2.0
    if question and question.lower() in lower:
        score += 6.0
    if session.get("focus_clip") == chunk.get("index"):
        score += 1.5
    return score

def select_focus_clips(question: str, chunks: list[dict], session: dict) -> list[dict]:
    if not chunks:
        return []

    q = clean_spaces(question).lower()
    if not q:
        idx = min(session.get("outline_cursor", 0), max(len(chunks) - 1, 0))
        return [chunks[idx]]

    if any(term in q for term in ["overview", "summary", "big picture", "what is this lesson", "start from the beginning"]):
        cursor = session.get("outline_cursor", 0)
        picks = []
        for idx in [cursor, cursor + 1]:
            if 0 <= idx < len(chunks):
                picks.append(chunks[idx])
        return picks or [chunks[0]]

    if any(term in q for term in ["again", "rewind", "previous", "last part"]) and session.get("focus_clip") is not None:
        focus_idx = int(session["focus_clip"])
        nearby = [chunk for chunk in chunks if abs(chunk["index"] - focus_idx) <= 1]
        return nearby or [chunks[max(0, min(focus_idx, len(chunks) - 1))]]

    tokens = tokenize(question)
    ranked = sorted(chunks, key=lambda chunk: clip_score(question, tokens, chunk, session), reverse=True)
    best = ranked[0]
    if clip_score(question, tokens, best, session) <= 0:
        cursor = min(session.get("outline_cursor", 0), len(chunks) - 1)
        return [chunks[cursor]]

    selected = [chunk for chunk in chunks if abs(chunk["index"] - best["index"]) <= 1]
    return selected[:3] or [best]

def infer_takeaway(question: str, focus_text: str, title: str) -> str:
    q = question.lower()
    if "why" in q:
        return f"The lesson is answering why this matters in {title.lower()} by tying the source moment to the underlying structure."
    if "how" in q:
        return "Focus on the sequence the creator follows: represent the input, transform it, then interpret the result."
    if any(term in q for term in ["difference", "compare", "versus", "vs"]):
        return "Treat the explanation as a comparison problem: which pieces stay fixed, which pieces change, and what each one contributes."
    return sentence_case(trim_sentence(focus_text, 150))

def extract_terms(text: str, title: str, limit: int = 4) -> list[str]:
    ordered = []
    seen = set()
    for token in tokenize(f"{title} {text}"):
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
        if len(ordered) >= limit:
            break
    return ordered

def find_term_context(term: str, clips: list[dict]) -> str:
    for clip in clips:
        if term.lower() in clip.get("text", "").lower():
            return trim_sentence(clip["text"], 95)
    return "This term anchors the current explanation."

def mermaid_safe(text: str, limit: int = 40) -> str:
    safe = clean_spaces(text)
    safe = re.sub(r"[\[\]\{\}\(\)\|\"']", "", safe)
    return trim_sentence(safe, limit).replace("...", "")

def build_story_metrics(question: str, focus_text: str, supporting: list[str], terms: list[str]) -> list[dict]:
    source_density = clamp_metric(48 + min(len(focus_text.split()), 28), 42, 92)
    pattern_depth = clamp_metric(52 + len(terms) * 8 + len(supporting) * 5, 44, 94)
    actionability = clamp_metric(58 + (10 if any(term in question.lower() for term in ["how", "step", "example"]) else 0), 46, 96)
    retention = clamp_metric(55 + min(len(tokenize(focus_text)), 20), 45, 93)
    return [
        {"label": "Source grounding", "value": source_density, "color": "#4cc9f0"},
        {"label": "Pattern depth", "value": pattern_depth, "color": "#f72585"},
        {"label": "Action path", "value": actionability, "color": "#fca311"},
        {"label": "Recall boost", "value": retention, "color": "#80ed99"},
    ]

def clamp_metric(value: int, low: int = 20, high: int = 100) -> int:
    return max(low, min(high, int(value)))

def build_mermaid_diagram(title: str, creator: str, focus_text: str, supporting: list[str], takeaway: str) -> str:
    first = mermaid_safe(focus_text, 42)
    support_a = mermaid_safe(supporting[0], 36) if supporting else "Pattern becomes visible"
    support_b = mermaid_safe(supporting[1], 36) if len(supporting) > 1 else f"{creator} adds context"
    result = mermaid_safe(takeaway, 46)
    lesson = mermaid_safe(title, 34)
    return "\n".join(
        [
            "flowchart LR",
            f'    A["Source clip<br/>{first}"] --> B["Interpretation<br/>{support_a}"]',
            f'    B --> C["Expansion<br/>{support_b}"]',
            f'    C --> D["Learning outcome<br/>{result}"]',
            f'    D --> E["Next move<br/>{lesson}"]',
        ]
    )

def build_reactflow_payload(flow_nodes: list[dict]) -> dict:
    nodes = []
    edges = []
    for idx, node in enumerate(flow_nodes):
        node_id = f"n{idx}"
        nodes.append(
            {
                "id": node_id,
                "position": {"x": 90 + idx * 220, "y": 110 if idx % 2 == 0 else 260},
                "data": {
                    "label": node["title"],
                    "text": trim_sentence(node["text"], 90),
                    "tone": ["cyan", "pink", "amber", "lime"][idx % 4],
                },
            }
        )
        if idx:
            edges.append(
                {
                    "id": f"e{idx-1}-{idx}",
                    "source": f"n{idx-1}",
                    "target": node_id,
                    "animated": True,
                    "label": "signals",
                }
            )
    return {"nodes": nodes, "edges": edges}

def build_board_actions(question: str, title: str, focus_clips: list[dict]) -> list[dict]:
    if not focus_clips:
        return [
            {"type": "title", "text": title or "Lesson focus"},
            {"type": "bullet", "text": sentence_case(trim_sentence(question or "Start with the main idea and build from there.", 120))},
            {"type": "highlight", "text": "One clean board sketch at a time."},
        ]

    focus = focus_clips[0]
    first_line = sentence_case(trim_sentence(focus["text"], 128))
    actions = [
        {"type": "title", "text": trim_sentence(title or focus["text"], 72)},
        {"type": "bullet", "text": first_line},
    ]
    if len(focus_clips) > 1:
        actions.append({"type": "bullet", "text": sentence_case(trim_sentence(focus_clips[1]["text"], 118))})
    else:
        actions.append({"type": "bullet", "text": infer_takeaway(question, focus["text"], title or "the lesson")})
    actions.append({"type": "highlight", "text": "Keep your eye on the core idea, then follow the change step by step."})
    return actions

def build_visual_payload(question: str, video: dict | None, focus_clips: list[dict], board_actions: list[dict]) -> dict:
    title = (video or {}).get("title", "Lesson")
    answer = board_actions[-1]["text"] if board_actions else infer_takeaway(question, focus_clips[0]["text"] if focus_clips else title, title)
    focus_text = focus_clips[0]["text"] if focus_clips else answer
    supporting = [sentence_case(trim_sentence(chunk["text"], 98)) for chunk in focus_clips[1:3]]
    return build_blackboard_visual_payload(
        title=title,
        question=question or title,
        answer=answer,
        focus_text=focus_text,
        supporting=supporting,
    )

def build_suggestions(question: str, focus_clips: list[dict]) -> list[str]:
    base = [
        "Give me the big picture",
        "Use a concrete example",
        "Turn this into steps",
        "Show the board version",
    ]
    q = question.lower()
    if "example" in q:
        base[1] = "Compare it to another example"
    if any(term in q for term in ["why", "reason"]):
        base[3] = "Show the cause and effect"
    if not focus_clips:
        base[2] = "Start with the first concept"
    else:
        base[2] = "Replay the source moment"
    return base

def build_reference_bridge(video: dict | None, focus_clips: list[dict]) -> dict | None:
    if not focus_clips:
        return None
    primary = focus_clips[0]
    summary = trim_sentence(primary["text"], 126)
    return {
        "start_sec": max(0.0, float(primary.get("start_sec", 0.0)) - 1.5),
        "end_sec": max(float(primary.get("end_sec", 0.0)), float(primary.get("start_sec", 0.0)) + 8.0),
        "label": trim_sentence(summary, 64),
        "intro_text": f"I will show you the relevant moment in the video first. Listen for {summary.lower()}, and when it ends I will break it down on the board.",
        "source_excerpt": summary,
    }

def build_answer_text(question: str, video: dict | None, focus_clips: list[dict]) -> str:
    title = (video or {}).get("title", "this lesson")
    if not focus_clips:
        return (
            f"Let me answer it in the clearest classroom way. In {title}, the useful move is to start from the core idea, make it concrete, and then connect it to what you actually need to understand. "
            "Ask for the exact part that feels confusing, and I will break it down step by step for you."
        )

    primary = sentence_case(trim_sentence(focus_clips[0]["text"], 180))
    support = [sentence_case(trim_sentence(chunk["text"], 150)) for chunk in focus_clips[1:3]]
    q = question.lower()

    if "overview" in q or "big picture" in q or "what is this lesson" in q:
        lines = [f"The big picture is this. {primary}"]
        if support:
            lines.append(f"Then the lesson widens the view by adding {support[0].lower()}")
        lines.append("So the thread for you to hold onto is the main idea first, and the details second.")
        return " ".join(lines)

    if "example" in q:
        return (
            f"A simple way to see it is this. {primary} "
            f"{support[0] if support else 'Now imagine the same pattern in one smaller, concrete case.'} "
            "That is usually the point where the idea stops feeling abstract and starts feeling usable for you."
        )

    if "why" in q:
        lines = [
            f"Here is why it matters. {primary}",
            "That piece is doing real work in the explanation, not just adding vocabulary.",
        ]
        if support:
            lines.append("You can feel that more clearly when the lesson adds " + support[0].lower())
        return " ".join(lines)

    if "difference" in q or "compare" in q or "vs" in q or "versus" in q:
        other = support[0] if support else "the surrounding clip"
        return (
            f"The clean comparison is this. {primary} "
            f"Then set it beside {other.lower()} "
            "Compare what stays fixed, what changes, and what job each part is doing in the explanation."
        )

    return (
        f"Here is the short answer. {primary} "
        f"{support[0] if support else 'What makes it click for you is seeing how the pieces connect instead of memorizing them one by one.'} "
        "Once that part is clear, the rest of the lesson becomes much easier to follow."
    )

def build_follow_up(question: str) -> str:
    q = question.lower()
    if "example" in q:
        return "Do you want another example, or should I connect this back to the source moment?"
    if "why" in q:
        return "Should I go deeper into the intuition, or should I draw the cause and effect on the board?"
    return "Do you want a slower walkthrough, a replay, or a bigger-picture summary next?"

def build_greeting(video: dict | None, avatar: dict, context_lines: list[str]) -> str:
    del avatar
    creator_name = (video or {}).get("creator_name", "the creator")
    creator_profession = (video or {}).get("creator_profession", "educator")
    title = (video or {}).get("title", "this lesson")
    lesson_context = " ".join(context_lines[:2]) or "the main ideas of this lesson together."
    return (
        f'I am the AI clone of "{creator_name}" for the video "{title}". '
        f'Today we are going to learn {lesson_context} '
        f'Ask me anything the way you would ask a real {creator_profession} in class, and I will walk through it with you on the board.'
    )

def build_greeting_payload(video: dict | None, avatar: dict, chunks: list[dict]) -> dict:
    outline = choose_outline(chunks)
    context_lines = lesson_context_lines(chunks, limit=2)
    board_actions = [
        {"type": "clear"},
        {"type": "title", "text": (video or {}).get("title", "Lesson")},
    ]
    board_actions.extend({"type": "bullet", "text": sentence_case(item["title"])} for item in outline[:2])
    board_actions.append({"type": "highlight", "text": "Ask naturally and I will explain it with one clean board sketch at a time."})
    text = build_greeting(video, avatar, context_lines)
    visual_payload = build_blackboard_visual_payload(
        title=(video or {}).get("title", "Lesson"),
        question="What will this lesson cover?",
        answer=text,
        focus_text=" ".join(context_lines[:2]) or text,
        supporting=context_lines[1:2],
    )
    return {
        "greeting": text,
        "text": text,
        "board_actions": board_actions,
        "visual_payload": visual_payload,
        "suggestions": [
            "Give me the big picture",
            "Start with the first concept",
            "What should I notice first?",
            "Replay the opening clip",
        ],
        "lesson_outline": outline,
    }

def build_chat_payload(question: str, video: dict | None, session: dict) -> dict:
    chunks = load_video_chunks(video)
    focus_clips = select_focus_clips(question, chunks, session)
    if focus_clips and "index" in focus_clips[0]:
        session["focus_clip"] = focus_clips[0]["index"]
        session["outline_cursor"] = min(int(focus_clips[0]["index"]) + 1, max(len(chunks) - 1, 0))
    elif chunks:
        session["outline_cursor"] = min(session.get("outline_cursor", 0) + 1, len(chunks) - 1)
    persist_session_data(session)

    board_actions = build_board_actions(question, (video or {}).get("title", "Lesson"), focus_clips)
    timestamp = focus_clips[0]["start_sec"] if focus_clips else None
    timestamps = [float(item.get("start_sec", 0.0)) for item in focus_clips[:3]]
    return {
        "answer": build_answer_text(question, video, focus_clips),
        "follow_up": build_follow_up(question),
        "suggestions": build_suggestions(question, focus_clips),
        "timestamp": timestamp,
        "timestamps": timestamps,
        "source": "video",
        "board_actions": board_actions,
        "visual_payload": build_visual_payload(question, video, focus_clips, board_actions),
        "reference_bridge": build_reference_bridge(video, focus_clips),
    }


def build_non_video_chat_payload(question: str, video: dict | None) -> dict:
    title = (video or {}).get("title", "Lesson")
    answer = sentence_case(
        trim_sentence(
            f"Here is the simple version of {question}. Start with the core idea, then connect it to why it matters in {title.lower()}.",
            220,
        )
    )
    board_actions = [
        {"type": "clear"},
        {"type": "title", "text": title},
        {"type": "bullet", "text": sentence_case(trim_sentence(question, 120))},
        {"type": "highlight", "text": answer},
    ]
    return {
        "answer": answer,
        "follow_up": "Would you like a deeper explanation, or does that already make sense?",
        "suggestions": ["Explain in more detail", "I understand", "Give me an example"],
        "timestamp": None,
        "timestamps": [],
        "source": "classroom",
        "board_actions": board_actions,
        "visual_payload": build_blackboard_visual_payload(
            title=title,
            question=question,
            answer=answer,
            focus_text=question,
            supporting=["Direct teaching mode is active."],
        ),
        "reference_bridge": None,
    }

async def safe_speak(
    session_id: str,
    text: str,
    voice_id: str,
    lang: str,
    fallback_voice: str | None = None,
) -> dict | None:
    if not text:
        return None
    try:
        return await speak_text(session_id, text, voice_id, lang, fallback_voice=fallback_voice)
    except Exception as exc:
        print(f"Audio synthesis failed: {exc}")
        return None


async def safe_speak_segments(
    session_id: str,
    segments: list[dict],
    voice_id: str,
    lang: str,
    fallback_voice: str | None = None,
) -> list[dict]:
    if not segments:
        return []
    try:
        return await speak_segments(session_id, segments, voice_id, lang, fallback_voice=fallback_voice)
    except Exception as exc:
        print(f"Segment audio synthesis failed: {exc}")
        return []


async def safe_speak_segment(
    session_id: str,
    segment: dict[str, Any],
    voice_id: str,
    lang: str,
    fallback_voice: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(segment, dict):
        return None
    text = clean_spaces(segment.get("speech_text") or segment.get("text"))
    if not text:
        return None
    try:
        audio = await speak_text(session_id, text, voice_id, lang, fallback_voice=fallback_voice)
        return {
            "segment_id": segment.get("segment_id"),
            "label": segment.get("label"),
            "speech_text": text,
            "frame_goal": segment.get("frame_goal"),
            "timing_hint": segment.get("timing_hint"),
            "audio_url": audio.get("audio_url") if audio else None,
        }
    except Exception as exc:
        print(f"Segment audio synthesis failed for {segment.get('segment_id')}: {exc}")
        return None


def json_stream_line(event: str, data: dict[str, Any]) -> bytes:
    return (json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n").encode("utf-8")


async def prepare_streaming_segment_item(
    *,
    question: str,
    blueprint: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any] | None:
    lesson_plan = blueprint.get("lesson_plan") if isinstance(blueprint.get("lesson_plan"), dict) else {}
    segment_plan = blueprint.get("segment_plan") if isinstance(blueprint.get("segment_plan"), dict) else {}
    storyboard = blueprint.get("storyboard") if isinstance(blueprint.get("storyboard"), dict) else {}
    preferred_visualization = normalize_visualization_preference(blueprint.get("preferred_visualization"))
    preplanned_frames = [item for item in (blueprint.get("frame_sequence") or []) if isinstance(item, dict)]

    async def build_frame() -> dict[str, Any]:
        if frame_number - 1 < len(preplanned_frames):
            return await materialize_frame_plan(preplanned_frames[frame_number - 1])
        plan = await plan_frame_for_segment(
            question=question,
            lesson_plan=lesson_plan,
            segment=segment,
            frame_number=frame_number,
            segment_plan=segment_plan,
            storyboard=storyboard,
            preferred_visualization=preferred_visualization,
        )
        return await materialize_frame_plan(plan)

    frame_plan = await build_frame()
    visual_segment = build_visual_segment_for_frame(segment_plan, frame_plan, frame_number)
    if not visual_segment:
        return None
    return {
        "segment_index": frame_number,
        "segment": segment,
        "frame_plan": frame_plan,
        "visual_segment": visual_segment,
    }


async def generated_media_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(20 * 60)
        try:
            cleanup_expired_generated_media()
        except Exception as exc:  # noqa: BLE001
            logger.warning("generated media periodic cleanup failed: %s", exc)

@app.on_event("startup")
async def startup():
    ensure_dirs()
    load_persisted_sessions()
    cleanup_sessions()
    video_count = len(load_videos())
    print(f"Parallea V2 Layered booting with {video_count} videos")
    logger.info(
        "startup complete videos=%s lesson_teacher=%s video_teacher=%s openai_pipeline=%s",
        video_count,
        bool(get_lesson_teacher_response_async),
        bool(get_teaching_response_async),
        openai_pipeline_status(),
    )
    logger.info("startup ai-config provider=openai pipeline=%s", openai_pipeline_status())
    log_admin_auth_status()
    log_supabase_analytics_status()
    log_storage_status()
    manim_info = log_manim_runtime_status()
    cleanup_expired_generated_media()
    asyncio.create_task(generated_media_cleanup_loop())
    diag = tts_diagnostics()
    if diag["configured"]:
        print("TTS configuration ready")
    else:
        print(f"TTS configuration incomplete: missing {', '.join(diag['missing'])}")
    if not manim_info.get("manim_importable"):
        print("Manim runtime probe failed; use /health/manim for full diagnostics.")

@app.get("/health")
def health():
    diag = tts_diagnostics()
    return {"status": "ok", "videos": len(load_videos()), "tts": diag, "manim": manim_runtime_info(), "openai": openai_pipeline_status()}


@app.get("/health/storage")
def health_storage():
    return {"status": "ok", "storage": storage_status()}


@app.get("/health/manim")
def health_manim(render: bool = Query(False)):
    runtime = manim_runtime_info()
    if not render:
        healthy = bool(runtime.get("manim_importable")) and not runtime.get("latex_required_missing")
        return {"status": "ok" if healthy else "degraded", "runtime": runtime}
    try:
        result = render_manim_healthcheck()
        return {
            "status": "ok",
            "runtime": runtime,
            "render": result,
        }
    except Exception as exc:
        logger.exception("manim health check failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "runtime": runtime,
                "error": str(exc),
            },
        )


@app.get("/api/dev/manim-health")
def api_dev_manim_health():
    from manim_renderer import has_latex_available, latex_runtime_info, manim_allow_mathtex_effective_value, manim_text_only_mode
    from config import MANIM_PUBLIC_OUTPUT_DIR, MANIM_PUBLIC_BASE_URL, MANIM_ENABLED, MANIM_FORCE_TEXT_ONLY, MANIM_ALLOW_MATHTEX, MANIM_REQUIRE_LATEX
    runtime = manim_runtime_info()
    base = {
        "manimAvailable": bool(runtime.get("manim_importable")),
        "manimEnabled": bool(MANIM_ENABLED),
        "manimVersion": runtime.get("manim_version"),
        "pythonExecutable": runtime.get("python_executable"),
        "workingDirectory": str(BASE_DIR),
        "outputDir": str(MANIM_PUBLIC_OUTPUT_DIR),
        "publicBaseUrl": MANIM_PUBLIC_BASE_URL,
        "latexAvailable": has_latex_available(),
        "latexPath": latex_runtime_info().get("latex_path") or "",
        "dvisvgmAvailable": bool(latex_runtime_info().get("dvisvgm_available")),
        "dvisvgmPath": latex_runtime_info().get("dvisvgm_path") or "",
        "mathtexEffective": manim_allow_mathtex_effective_value(),
        "textOnlyMode": manim_text_only_mode(),
        "envFlags": {
            "MANIM_ENABLED": MANIM_ENABLED,
            "MANIM_FORCE_TEXT_ONLY": MANIM_FORCE_TEXT_ONLY,
            "MANIM_ALLOW_MATHTEX": MANIM_ALLOW_MATHTEX,
            "MANIM_REQUIRE_LATEX": MANIM_REQUIRE_LATEX,
        },
    }
    if not MANIM_ENABLED:
        return {**base, "status": "disabled", "textSceneRendered": False, "publicUrl": None, "outputPath": None}
    try:
        result = render_manim_healthcheck()
        media_path = result.get("media_path") or ""
        media_exists = bool(media_path and Path(media_path).exists())
        public_url = result.get("video_url") or result.get("media_url") or "/generated/manim/test/scene.mp4"
        return {
            **base,
            "status": "ok",
            "textSceneRendered": bool(result.get("text_scene_rendered")),
            "outputPath": media_path,
            "outputExists": media_exists,
            "publicUrl": public_url,
            "browserUrl": public_url,
            "render": result,
            "runtime": runtime,
        }
    except Exception as exc:
        logger.exception("dev manim health check failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                **base,
                "status": "failed",
                "textSceneRendered": False,
                "outputExists": False,
                "publicUrl": "/generated/manim/test/scene.mp4",
                "browserUrl": "/generated/manim/test/scene.mp4",
                "runtime": runtime,
                "error": str(exc),
            },
        )


@app.get("/auth.css")
def auth_css():
    path = BASE_DIR / "auth.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="auth.css missing")
    return FileResponse(str(path), media_type="text/css")


@app.get("/auth.js")
def auth_js():
    path = BASE_DIR / "auth.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="auth.js missing")
    return FileResponse(str(path), media_type="application/javascript")


@app.get("/teacher.css")
def teacher_css():
    path = BASE_DIR / "teacher.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="teacher.css missing")
    return FileResponse(str(path), media_type="text/css")


@app.get("/teacher.js")
def teacher_js():
    path = BASE_DIR / "teacher.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="teacher.js missing")
    return FileResponse(str(path), media_type="application/javascript")


@app.get("/student.css")
def student_css():
    path = BASE_DIR / "student.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="student.css missing")
    return FileResponse(str(path), media_type="text/css")


@app.get("/student.js")
def student_js():
    path = BASE_DIR / "student.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="student.js missing")
    return FileResponse(str(path), media_type="application/javascript")


@app.get("/student-learn.css")
def student_learn_css():
    path = BASE_DIR / "student-learn.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="student-learn.css missing")
    return FileResponse(str(path), media_type="text/css")


@app.get("/student-learn.js")
def student_learn_js():
    path = BASE_DIR / "student-learn.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="student-learn.js missing")
    return FileResponse(str(path), media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    return resolve_html(INDEX_HTML)


def _persona_first_redirect(
    request: Request,
    *,
    teacher_target: str = "/teacher/dashboard",
    student_target: str | None = None,
) -> RedirectResponse:
    """Redirect old video-first entry points to the persona-first product."""
    try:
        from backend.auth.dependencies import current_user as _current_user
        user = _current_user(request)
    except Exception:
        user = None
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.get("role") in {"teacher", "admin"}:
        return RedirectResponse(teacher_target, status_code=302)
    return RedirectResponse(student_target or "/student/personas", status_code=302)


@app.get("/player", response_class=HTMLResponse)
def player_page(request: Request):
    return _persona_first_redirect(request)


@app.get("/player.html", response_class=HTMLResponse)
def player_html_page(request: Request):
    return _persona_first_redirect(request)

@app.get("/avatar-select", response_class=HTMLResponse)
def avatar_select_page(request: Request):
    return _persona_first_redirect(request)


@app.get("/avatar-select.html", response_class=HTMLResponse)
def avatar_select_html_page(request: Request):
    return _persona_first_redirect(request)

@app.get("/learn", response_class=HTMLResponse)
def learn_page(request: Request, video: str | None = Query(default=None)):
    """Legacy entry point.

    The new home is /student/learn/{personaId}. If `?video=<id>` is provided,
    redirect to the matching teacher persona's immersive page so old links
    keep working. Otherwise send the user to persona browse.
    """
    try:
        from backend.auth.dependencies import current_user as _current_user
        user = _current_user(request)
    except Exception:
        user = None
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    if user.get("role") in {"teacher", "admin"}:
        return RedirectResponse("/teacher/dashboard", status_code=302)
    if video:
        from backend.store import videos_repo as _videos_repo  # local import avoids ordering issues
        row = _videos_repo.get(video)
        persona_id = (row or {}).get("persona_id") if row else None
        if persona_id:
            return RedirectResponse(f"/student/learn/{persona_id}", status_code=302)
    return RedirectResponse("/student/personas", status_code=302)


@app.get("/learn.html", response_class=HTMLResponse)
def learn_html_page(request: Request, video: str | None = Query(default=None)):
    return learn_page(request, video=video)


@app.get("/watch/{video_id}", response_class=HTMLResponse)
def watch_video_page(request: Request, video_id: str):  # noqa: ARG001
    return _persona_first_redirect(request)


@app.get("/upload", response_class=HTMLResponse)
def legacy_upload_page(request: Request):
    return _persona_first_redirect(request, teacher_target="/teacher/upload")

@app.get("/videos")
def videos(request: Request):
    return _persona_first_redirect(request)

@app.get("/video-meta/{video_id}")
def video_meta(request: Request, video_id: str):  # noqa: ARG001
    return _persona_first_redirect(request)

@app.get("/thumbnail/{video_id}")
def thumbnail(video_id: str):
    teacher_video = teacher_videos_repo.get(video_id)
    if teacher_video and teacher_video.get("thumbnail_storage_backend") == "s3" and teacher_video.get("thumbnail_object_key"):
        try:
            return RedirectResponse(url_for_object(str(teacher_video["thumbnail_object_key"])), status_code=302)
        except Exception as exc:  # noqa: BLE001
            logger.exception("thumbnail storage URL unavailable video_id=%s: %s", video_id, exc)
            raise HTTPException(status_code=503, detail="Thumbnail storage URL unavailable") from exc
    path = THUMBNAILS_DIR / f"{video_id}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path)

@app.get("/video/{video_id}")
def stream_video(video_id: str, request: Request):
    teacher_video = teacher_videos_repo.get(video_id)
    if teacher_video and teacher_video.get("storage_backend") == "s3" and teacher_video.get("object_key"):
        try:
            return RedirectResponse(url_for_object(str(teacher_video["object_key"])), status_code=302)
        except Exception as exc:  # noqa: BLE001
            logger.exception("video storage URL unavailable video_id=%s: %s", video_id, exc)
            raise HTTPException(status_code=503, detail="Video storage URL unavailable") from exc
    video = teacher_video or find_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    path = UPLOADS_DIR / video["filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video file missing")
    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    range_header = request.headers.get("range")
    if range_header:
        _, range_spec = range_header.split("=")
        start_s, end_s = range_spec.split("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)
        chunk_size = end - start + 1

        def iter_file():
            with path.open("rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
        }
        return StreamingResponse(iter_file(), status_code=206, headers=headers, media_type=media_type)
    return FileResponse(path, media_type=media_type)

@app.get("/audio-response/{name}")
def serve_audio(name: str):
    path = AUDIO_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    media_type = mimetypes.guess_type(path.name)[0] or ("audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg")
    return FileResponse(path, media_type=media_type)


@app.get("/audio-status/{job_id}")
def audio_status(job_id: str):
    job = _audio_jobs.get(job_id)
    if not isinstance(job, dict):
        return {"ready": False}
    if job.get("ready") and job.get("url"):
        return {"ready": True, "url": job.get("url")}
    if job.get("error"):
        return {"ready": False, "error": job.get("error")}
    return {"ready": False}

@app.get("/avatar-presets")
def avatar_presets():
    payload = []
    for avatar in AVATAR_PRESETS:
        payload.append(
            {
                **avatar,
                "sample_audio_url": cached_avatar_audio_url("avatar_preview", avatar),
                "greeting_audio_url": cached_avatar_audio_url("avatar_greeting", avatar),
            }
        )
    return JSONResponse(payload)

@app.post("/warm-avatar-audio")
async def warm_avatar_audio():
    return {"warmed": await warm_avatar_audio_cache()}

@app.post("/avatar-sample")
async def avatar_sample(payload: Dict[str, Any] = Body(...)):
    avatar_id = payload.get("avatar_id")
    sid = payload.get("sid") or f"s_{uuid.uuid4().hex[:10]}"
    avatar = avatar_by_id(avatar_id)
    line = avatar_preview_text(avatar)
    audio = await ensure_avatar_audio("avatar_preview", avatar)
    greeting = await ensure_avatar_audio("avatar_greeting", avatar)
    return {
        "sid": sid,
        "text": line,
        "audio_url": audio.get("audio_url") if audio else None,
        "greeting_audio_url": greeting.get("audio_url") if greeting else None,
        "avatar": avatar,
    }

@app.post("/set-avatar")
async def set_avatar(payload: Dict[str, Any] = Body(...)):
    sid = payload.get("sid")
    avatar_id = payload.get("avatar_id")
    if not sid or not avatar_id:
        raise HTTPException(status_code=400, detail="sid and avatar_id are required")
    avatar = avatar_by_id(avatar_id)
    sess = get_session(sid)
    sess["avatar_id"] = avatar["id"]
    sess["voice_id"] = avatar_voice_id(avatar)
    sess["voice_label"] = avatar.get("voice", "")
    sess["voice_lang"] = avatar.get("lang", "en-us")
    sess["edge_voice"] = avatar.get("edge_voice")
    persist_session_data(sess)
    return {"ok": True, "sid": sid, "avatar": avatar}

@app.get("/session-state/{sid}")
def session_state(sid: str):
    return get_session(sid)


@app.get("/dev/voice-test", response_class=HTMLResponse)
def dev_voice_test():
    if os.getenv("PARALLEA_ENV", "").strip().lower() in {"prod", "production"}:
        raise HTTPException(status_code=404, detail="not found")
    if not DEV_VOICE_TEST_HTML.exists():
        raise HTTPException(status_code=404, detail="dev voice test page missing")
    return HTMLResponse(DEV_VOICE_TEST_HTML.read_text(encoding="utf-8"))


@app.post("/transcribe-question")
async def transcribe_question_ep(
    audio: UploadFile = File(...),
    client_duration_ms: float = Form(default=0),
    client_mime_type: str = Form(default=""),
    client_sample_rate: float = Form(default=0),
    client_chunks: int = Form(default=0),
    client_session_state: str = Form(default=""),
):
    suffix = Path(audio.filename or "question.webm").suffix or ".webm"
    temp = AUDIO_DIR / f"mic_{uuid.uuid4().hex}{suffix}"
    filename = audio.filename or "question.webm"
    upload_mime = audio.content_type or client_mime_type or ""
    logger.info(
        "transcribe request received filename=%s content_type=%s client_mime=%s client_duration_ms=%.0f sample_rate=%.0f chunks=%s state=%s temp=%s",
        filename,
        audio.content_type or "",
        client_mime_type,
        client_duration_ms,
        client_sample_rate,
        client_chunks,
        client_session_state,
        temp.name,
    )
    with temp.open("wb") as f:
        shutil.copyfileobj(audio.file, f)
    try:
        dev_debug = os.getenv("PARALLEA_ENV", "").strip().lower() not in {"prod", "production"}
        size_bytes = temp.stat().st_size if temp.exists() else 0
        if size_bytes < 2048:
            logger.warning("transcribe rejected empty audio filename=%s bytes=%s", filename, size_bytes)
            return {
                "question": "",
                "rawTranscript": "",
                "needsConfirmation": True,
                "unclearReason": "empty_audio" if size_bytes <= 0 else "audio_too_small",
                "error": "empty_audio" if size_bytes <= 0 else "audio_too_small",
                "message": "The microphone audio was empty or too short. Please try again.",
                "metadata": {
                    "audio": {
                        "size_bytes": size_bytes,
                        "client_duration_ms": client_duration_ms,
                        "client_mime_type": client_mime_type,
                        "client_sample_rate": client_sample_rate,
                        "client_chunks": client_chunks,
                        "client_session_state": client_session_state,
                    },
                    "stt": stt_provider_status(),
                },
            }
        source_duration = audio_duration_seconds(str(temp))
        result = transcribe_question_result(str(temp), original_mime_type=upload_mime, original_filename=filename)
        text = (result.get("text") or "").strip()
        logger.info(
            "transcribe success filename=%s bytes=%s source_duration=%s provider=%s model=%s language=%s chars=%s confidence=%s no_speech_prob=%s needs_confirmation=%s reason=%s transcript=%r",
            filename,
            size_bytes,
            source_duration,
            result.get("provider"),
            result.get("model"),
            result.get("language"),
            len(text),
            result.get("confidence"),
            result.get("no_speech_prob"),
            result.get("needs_confirmation"),
            result.get("unclear_reason"),
            text[:160],
        )
        return {
            "question": text,
            "rawTranscript": text,
            "needsConfirmation": result.get("needs_confirmation", False),
            "unclearReason": result.get("unclear_reason"),
            "metadata": {
                "audio": {
                    **(result.get("audio") or {}),
                    "client_duration_ms": client_duration_ms,
                    "client_mime_type": client_mime_type,
                    "client_sample_rate": client_sample_rate,
                    "client_chunks": client_chunks,
                    "client_session_state": client_session_state,
                    "source_duration_sec": source_duration,
                },
                "stt": {
                    "provider": result.get("provider"),
                    "model": result.get("model"),
                    "language": result.get("language"),
                    "confidence": result.get("confidence"),
                    "avg_logprob": result.get("avg_logprob"),
                    "no_speech_prob": result.get("no_speech_prob"),
                    "segments": result.get("segments"),
                },
            },
        }
    except AudioInputError as exc:
        logger.warning("transcribe rejected invalid audio filename=%s code=%s debug=%s", filename, exc.code, exc.debug)
        content = {
            "question": "",
            "rawTranscript": "",
            "needsConfirmation": True,
            "unclearReason": exc.code,
            "error": exc.code,
            "message": exc.message,
            "metadata": {
                "stt": stt_provider_status(),
                "audio": {
                    "client_duration_ms": client_duration_ms,
                    "client_mime_type": client_mime_type,
                    "client_sample_rate": client_sample_rate,
                    "client_chunks": client_chunks,
                    "client_session_state": client_session_state,
                },
            },
        }
        if dev_debug:
            content["debug"] = exc.debug
        return JSONResponse(status_code=400, content=content)
    except AudioConversionError as exc:
        logger.exception("transcribe audio conversion failed filename=%s code=%s debug=%s", filename, exc.code, exc.debug)
        content = {
            "question": "",
            "rawTranscript": "",
            "needsConfirmation": True,
            "unclearReason": exc.code,
            "error": "audio_conversion_failed",
            "message": "Could not convert microphone audio. Please try again.",
            "metadata": {
                "stt": stt_provider_status(),
                "audio": {
                    "client_duration_ms": client_duration_ms,
                    "client_mime_type": client_mime_type,
                    "client_sample_rate": client_sample_rate,
                    "client_chunks": client_chunks,
                    "client_session_state": client_session_state,
                },
            },
        }
        if dev_debug:
            content["debug"] = {
                "inputSizeBytes": size_bytes,
                "mimeType": upload_mime,
                **(exc.debug or {}),
            }
        return JSONResponse(status_code=422, content=content)
    except VoicePipelineError as exc:
        logger.exception("transcribe voice pipeline failed filename=%s code=%s", filename, exc.code)
        content = {
            "question": "",
            "rawTranscript": "",
            "needsConfirmation": True,
            "unclearReason": exc.code,
            "error": exc.code,
            "message": exc.message,
            "metadata": {"stt": stt_provider_status()},
        }
        if dev_debug:
            content["debug"] = exc.debug
        return JSONResponse(status_code=500, content=content)
    except Exception as exc:
        logger.exception("transcribe failed filename=%s", filename)
        return JSONResponse(
            status_code=503,
            content={
                "question": "",
                "rawTranscript": "",
                "error": "Speech transcription failed on the server. Check Whisper and audio dependencies.",
                "detail": str(exc),
                "metadata": {
                    "stt": stt_provider_status(),
                    "audio": {
                        "client_duration_ms": client_duration_ms,
                        "client_mime_type": client_mime_type,
                        "client_sample_rate": client_sample_rate,
                        "client_chunks": client_chunks,
                        "client_session_state": client_session_state,
                    },
                },
            },
        )
    finally:
        temp.unlink(missing_ok=True)

@app.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    creator_name: str = Form(default="Creator"),
    creator_profession: str = Form(default="Educator"),
    title: str = Form(default=""),
):
    raise HTTPException(status_code=410, detail="Legacy public upload is disabled. Use /teacher/upload.")
    ensure_dirs()
    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    video_id = uuid.uuid4().hex[:8]
    filename = f"video_{video_id}{ext}"
    out_path = UPLOADS_DIR / filename
    with out_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    thumb_path = THUMBNAILS_DIR / f"{video_id}.jpg"
    extract_thumbnail(out_path, thumb_path)

    chunks = {}
    chunks_path = None
    try:
        chunks = transcribe_with_timestamps(str(out_path))
        if chunks:
            chunks_dir = DATA_DIR / f"video_{video_id}"
            chunks_path = chunks_dir / "chunks.json"
            save_chunks(chunks, output_path=str(chunks_path))
            try:
                build_index(video_id)
            except Exception as exc:
                logger.warning("vector index build failed video_id=%s error=%s", video_id, exc)
    except Exception as exc:
        print(f"Transcription failed for upload {video_id}: {exc}")

    item = {
        "id": video_id,
        "title": title or Path(file.filename or "").stem or f"Video {video_id}",
        "creator_name": creator_name,
        "creator_profession": clean_spaces(creator_profession) or "Educator",
        "creator": creator_name,
        "filename": filename,
        "uploaded_at": datetime.utcnow().isoformat(),
        "thumbnail_url": f"/thumbnail/{video_id}" if thumb_path.exists() else None,
        "has_transcript": bool(chunks),
        "chunks_path": normalize_chunks_path_value(chunks_path) if chunks_path else None,
    }
    items = load_videos()
    items = [v for v in items if v["id"] != video_id] + [item]
    save_videos(items)
    return JSONResponse(item)

@app.delete("/video/{video_id}")
async def delete_video(video_id: str):
    video = remove_video_from_index(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    removed = delete_video_storage(video)
    stale_sessions = [sid for sid, sess in _sessions.items() if sess.get("focus_video_id") == video_id]
    for sid in stale_sessions:
        remove_session(sid)

    return {
        "ok": True,
        "video_id": video_id,
        "title": video.get("title"),
        "removed": removed,
    }

async def build_greet_response(data: Dict[str, Any]) -> Dict[str, Any]:
    sid = data.get("sid") or data.get("session_id")
    lesson_id = clean_spaces(data.get("lesson_id"))
    video_id = data.get("video_id")
    logger.info(
        "greet request sid=%s mode=%s lesson_id=%s video_id=%s trigger=%s",
        sid or "-",
        "lesson" if lesson_id else "video",
        lesson_id or "-",
        video_id or "-",
        short_log_text(data.get("trigger")),
    )
    if lesson_id:
        if not sid:
            raise HTTPException(status_code=400, detail="sid is required")
        context = parse_lesson_context(data)
        if not context.get("lesson_title") or not context.get("current_section_title"):
            raise HTTPException(status_code=400, detail="lesson_title and current_section_title are required")
        sess = get_session(sid)
        sess["preferred_visualization"] = normalize_visualization_preference(
            data.get("preferred_visualization"),
            sess.get("preferred_visualization", ""),
        )
        previous_lesson_id = sess.get("lesson_id")
        sess["focus_video_id"] = None
        sess["lesson_section_id"] = context.get("current_section_id")
        persist_session_data(sess)
        if sess.get("greeted") and previous_lesson_id == lesson_id:
            return {"already_greeted": True}
        sess["lesson_id"] = lesson_id
        persist_session_data(sess)
        lesson_trigger = clean_spaces(data.get("trigger")) or "lesson_open"
        lesson_question = clean_spaces(data.get("question"))
        if lesson_trigger == "lesson_open":
            lesson_question = "Greet the learner, name the first lesson part, and tell them what to focus on first."
        guide_payload = None
        if get_lesson_teacher_response_async:
            try:
                guide_payload = await get_lesson_teacher_response_async(
                    question=lesson_question or "Greet the learner and introduce the current section briefly.",
                    lesson_title=context.get("lesson_title", "Lesson"),
                    lesson_description=context.get("lesson_description", ""),
                    current_section_title=context.get("current_section_title", "Current section"),
                    current_section_content=context.get("current_section_content", ""),
                    section_index=context.get("section_index", 0),
                    total_sections=context.get("total_sections", 1),
                    section_order=context.get("section_order", []),
                    visible_metadata=context.get("visible_metadata", []),
                    timestamps=context.get("timestamps", []),
                    conversation_history=sess["history"],
                    trigger=lesson_trigger,
                    preferred_visualization=sess.get("preferred_visualization", ""),
                )
            except Exception:
                logger.exception("lesson greet teacher call failed sid=%s lesson_id=%s", sid, lesson_id)
                guide_payload = None
        if not isinstance(guide_payload, dict):
            guide_payload = build_lesson_greeting_payload(context)
        spoken_greeting = (
            guide_payload.get("greeting")
            or guide_payload.get("answer")
            or guide_payload.get("text")
            or ""
        ).strip()
        audio = await safe_speak(sid, spoken_greeting, sess["voice_id"], sess["voice_lang"], sess.get("edge_voice"))
        append_history(sid, "assistant", spoken_greeting)
        sess["greeted"] = True
        persist_session_data(sess)
        logger.info(
            "greet response sid=%s mode=lesson lesson_id=%s chars=%s has_audio=%s",
            sid,
            lesson_id,
            len(spoken_greeting),
            bool(audio),
        )
        return {
            "greeting": spoken_greeting,
            "text": spoken_greeting,
            "suggestions": guide_payload.get("suggestions", LESSON_TUTOR_SUGGESTIONS),
            "board_actions": guide_payload.get("board_actions", []),
            "visual_payload": guide_payload.get("visual_payload", {"segments": []}),
            "has_audio": bool(audio),
            "audio_url": audio.get("audio_url") if audio else None,
            "source": "lesson",
            "session": sess,
        }
    if not sid or not video_id:
        raise HTTPException(status_code=400, detail="sid and video_id are required")
    sess = get_session(sid)
    sess["preferred_visualization"] = normalize_visualization_preference(
        data.get("preferred_visualization"),
        sess.get("preferred_visualization", ""),
    )
    avatar = avatar_by_id(sess["avatar_id"])
    video = find_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    sess["focus_video_id"] = video_id
    persist_session_data(sess)
    if sess.get("greeted"):
        return {"already_greeted": True}

    guide_payload = None
    chunks = load_video_chunks(video)
    lesson_context = lesson_context_text(chunks, limit=2)
    if get_greeting_async:
        try:
            guide_payload = await get_greeting_async(
                video.get("creator_name", "the creator"),
                video.get("title", "this video"),
                video.get("creator_profession", "educator"),
                lesson_context,
            )
        except Exception:
            logger.exception("video greet teacher call failed sid=%s video_id=%s", sid, video_id)
            guide_payload = None
    if not isinstance(guide_payload, dict):
        guide_payload = build_greeting_payload(video, avatar, chunks)

    spoken_greeting = (guide_payload.get("greeting") or guide_payload.get("text") or build_greeting(video, avatar, lesson_context_lines(chunks, limit=2))).strip()
    audio = None
    try:
        audio = await speak_cached(
            cache_key=f"lesson_greeting_{AVATAR_AUDIO_VERSION}_{video_id}_{avatar['id']}",
            text=spoken_greeting,
            voice_id=sess["voice_id"],
            lang=sess["voice_lang"],
            fallback_voice=sess.get("edge_voice"),
        )
    except Exception as exc:
        print(f"Dynamic lesson greeting audio failed: {exc}")
        audio = await safe_speak(sid, spoken_greeting, sess["voice_id"], sess["voice_lang"], sess.get("edge_voice"))
    append_history(sid, "assistant", spoken_greeting)
    sess["greeted"] = True
    persist_session_data(sess)
    logger.info(
        "greet response sid=%s mode=video video_id=%s chars=%s has_audio=%s",
        sid,
        video_id,
        len(spoken_greeting),
        bool(audio),
    )
    return {
        "greeting": spoken_greeting,
        "text": spoken_greeting,
        "suggestions": guide_payload.get("suggestions", []),
        "board_actions": guide_payload.get("board_actions", []),
        "visual_payload": guide_payload.get("visual_payload", {"segments": []}),
        "has_audio": bool(audio),
        "audio_url": audio.get("audio_url") if audio else None,
        "avatar": avatar,
        "session": sess,
    }

@app.post("/greet")
async def greet(request: Request):
    payload = await parse_request_payload(request)
    return await build_greet_response(payload)

async def build_chat_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    sid = payload.get("sid") or payload.get("session_id")
    lesson_id = clean_spaces(payload.get("lesson_id"))
    video_id = payload.get("video_id")
    question = (payload.get("question") or "").strip()
    requested_voice_id = payload.get("voice_id") or payload.get("voice_name")
    trigger = payload.get("trigger")
    logger.info(
        "chat request sid=%s mode=%s lesson_id=%s video_id=%s trigger=%s question=%s",
        sid or "-",
        "lesson" if lesson_id else "video",
        lesson_id or "-",
        video_id or "-",
        short_log_text(trigger),
        short_log_text(question),
    )
    if lesson_id:
        if not sid or not question:
            raise HTTPException(status_code=400, detail="sid, lesson_id, and question are required")
        context = parse_lesson_context(payload)
        if not context.get("lesson_title") or not context.get("current_section_title"):
            raise HTTPException(status_code=400, detail="lesson_title and current_section_title are required")
        sess = get_session(sid)
        teaching_session_state = ensure_teaching_session_state(sess)
        preferred_visualization = normalize_visualization_preference(
            payload.get("preferred_visualization"),
            sess.get("preferred_visualization", ""),
        )
        sess["preferred_visualization"] = preferred_visualization
        sess["focus_video_id"] = None
        sess["lesson_id"] = lesson_id
        sess["lesson_section_id"] = context.get("current_section_id")
        persist_session_data(sess)
        request_ctx = teaching_request_context(
            question,
            sess,
            source_mode="lesson",
            use_video_context=False,
            preferred_visualization=preferred_visualization,
        )
        append_history(sid, "user", question)
        answer_data = None
        if get_lesson_teacher_response_async:
            try:
                answer_data = await get_lesson_teacher_response_async(
                    question=request_ctx["effective_question"],
                    lesson_title=context.get("lesson_title", "Lesson"),
                    lesson_description=context.get("lesson_description", ""),
                    current_section_title=context.get("current_section_title", "Current section"),
                    current_section_content=context.get("current_section_content", ""),
                    section_index=context.get("section_index", 0),
                    total_sections=context.get("total_sections", 1),
                    section_order=context.get("section_order", []),
                    visible_metadata=context.get("visible_metadata", []),
                    timestamps=context.get("timestamps", []),
                    conversation_history=sess["history"],
                    trigger=clean_spaces(trigger) or None,
                    pedagogy_mode=request_ctx["pedagogy_mode"],
                    learner_request=request_ctx["learner_request"],
                    topic_question=request_ctx["base_question"],
                    preferred_visualization=request_ctx["preferred_visualization"],
                    session_state=teaching_session_state,
                )
            except Exception:
                logger.exception("lesson chat teacher call failed sid=%s lesson_id=%s", sid, lesson_id)
                answer_data = None
        fallback = build_lesson_chat_payload(request_ctx["effective_question"], context, clean_spaces(trigger))
        if not isinstance(answer_data, dict):
            answer_data = fallback
        answer = clean_spaces(answer_data.get("answer")) or fallback.get("answer", "")
        follow_up = clean_spaces(answer_data.get("follow_up")) or fallback.get("follow_up")
        suggestions = [clean_spaces(item) for item in (answer_data.get("suggestions") or []) if clean_spaces(item)] or fallback.get("suggestions", LESSON_TUTOR_SUGGESTIONS)
        timestamps = answer_data.get("timestamps") or fallback.get("timestamps", [])
        source = clean_spaces(answer_data.get("source")) or fallback.get("source", "lesson")
        board_actions = answer_data.get("board_actions") or fallback.get("board_actions", [])
        visuals = answer_data.get("visual_payload") or fallback.get("visual_payload", {"segments": []})
        context_label = clean_spaces(answer_data.get("context_label")) or fallback.get("context_label")
        speak_voice = requested_voice_id or sess["voice_id"]
        answer_audio = await safe_speak(sid, answer, speak_voice, sess["voice_lang"], sess.get("edge_voice"))
        append_history(sid, "assistant", answer)
        remember_teaching_loop(sess, request_ctx)
        if isinstance(answer_data.get("teaching_session_state"), dict):
            sess["teaching_session_state"] = answer_data["teaching_session_state"]
        sess["notes"].append(
            {
                "q": question,
                "a": answer,
                "lesson_id": lesson_id,
                "section_id": context.get("current_section_id"),
            }
        )
        if len(sess["notes"]) > MAX_NOTES:
            sess["notes"] = sess["notes"][-MAX_NOTES:]
        persist_session_data(sess)
        logger.info(
            "chat response sid=%s mode=lesson lesson_id=%s chars=%s has_audio=%s",
            sid,
            lesson_id,
            len(answer),
            bool(answer_audio),
        )
        return {
            "answer": answer,
            "follow_up": follow_up,
            "suggestions": suggestions,
            "timestamp": answer_data.get("timestamp"),
            "timestamps": timestamps,
            "source": source,
            "has_audio": bool(answer_audio),
            "board_actions": board_actions,
            "audio_url": answer_audio.get("audio_url") if answer_audio else None,
            "reference_bridge": answer_data.get("reference_bridge"),
            "visual_payload": visuals,
            "lesson_plan": answer_data.get("lesson_plan"),
            "teaching_segments": answer_data.get("teaching_segments", []),
            "frame_sequence": answer_data.get("frame_sequence", []),
            "audio_segments": [],
            "context_label": context_label,
            "pipeline_debug": answer_data.get("pipeline_debug"),
            "notes": sess["notes"],
            "transcript_log": sess["transcript_log"][-80:],
        }
    if not sid or not video_id or not question:
        raise HTTPException(status_code=400, detail="sid, video_id, and question are required")

    sess = get_session(sid)
    teaching_session_state = ensure_teaching_session_state(sess)
    video = find_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    preferred_visualization = normalize_visualization_preference(
        payload.get("preferred_visualization"),
        sess.get("preferred_visualization", ""),
    )
    sess["preferred_visualization"] = preferred_visualization
    sess["focus_video_id"] = video_id
    persist_session_data(sess)
    use_video_context = coerce_bool(payload.get("use_video_context"), sess.get("use_video_context", True))
    request_ctx = teaching_request_context(
        question,
        sess,
        source_mode="video",
        use_video_context=use_video_context,
        preferred_visualization=preferred_visualization,
    )
    append_history(sid, "user", question)

    answer_data = None
    fallback = build_chat_payload(request_ctx["base_question"], video, sess) if use_video_context else build_non_video_chat_payload(request_ctx["base_question"], video)
    if get_teaching_response_async:
        try:
            answer_data = await get_teaching_response_async(
                question=request_ctx["effective_question"],
                creator_name=video.get("creator_name", "the creator"),
                creator_profession=video.get("creator_profession", "educator"),
                video_title=video.get("title", "this video"),
                chunks_path=video.get("chunks_path") or f"video_{video_id}/chunks.json",
                conversation_history=sess["history"],
                trigger=trigger or None,
                use_video_context=use_video_context,
                pedagogy_mode=request_ctx["pedagogy_mode"],
                learner_request=request_ctx["learner_request"],
                topic_question=request_ctx["base_question"],
                preferred_visualization=request_ctx["preferred_visualization"],
                session_state=teaching_session_state,
            )
        except Exception:
            logger.exception("video chat teacher call failed sid=%s video_id=%s", sid, video_id)
            answer_data = None

    if not isinstance(answer_data, dict):
        answer_data = fallback

    answer = clean_spaces(answer_data.get("answer")) or fallback["answer"]
    follow_up = clean_spaces(answer_data.get("follow_up")) or fallback["follow_up"]
    suggestions = [clean_spaces(item) for item in (answer_data.get("suggestions") or []) if clean_spaces(item)] or fallback["suggestions"]
    board_actions = answer_data.get("board_actions") or fallback["board_actions"]
    visuals = answer_data.get("visual_payload") or fallback["visual_payload"]
    bridge = answer_data.get("reference_bridge") or fallback.get("reference_bridge")
    timestamps = answer_data.get("timestamps") or fallback.get("timestamps", [])
    source = clean_spaces(answer_data.get("source")) or fallback.get("source", "video")
    teaching_segments = [item for item in (answer_data.get("teaching_segments") or []) if isinstance(item, dict)]
    frame_sequence = [item for item in (answer_data.get("frame_sequence") or []) if isinstance(item, dict)]
    lesson_plan = answer_data.get("lesson_plan") if isinstance(answer_data.get("lesson_plan"), dict) else None
    if use_video_context and answer_data.get("timestamp") is not None and not bridge:
        ts = float(answer_data["timestamp"])
        bridge = {
            "start_sec": max(0.0, ts - 1.5),
            "end_sec": ts + 8.0,
            "label": "Relevant source moment",
            "intro_text": "I will show you the relevant moment in the video first. When it finishes, I will break it down on the board.",
        }

    bridge_audio_url = None
    if use_video_context and bridge and bridge.get("intro_text"):
        bridge_audio = await safe_speak(sid, bridge["intro_text"], sess["voice_id"], sess["voice_lang"], sess.get("edge_voice"))
        bridge_audio_url = bridge_audio.get("audio_url") if bridge_audio else None

    speak_voice = requested_voice_id or sess["voice_id"]
    audio_segments = []
    raw_segment_audio = await safe_speak_segments(sid, teaching_segments, speak_voice, sess["voice_lang"], sess.get("edge_voice"))
    if raw_segment_audio:
        audio_by_segment = {item.get("segment_id"): item for item in raw_segment_audio if isinstance(item, dict)}
        for segment in teaching_segments:
            audio_item = audio_by_segment.get(segment.get("segment_id"))
            if not audio_item:
                continue
            audio_segments.append(
                {
                    "segment_id": segment.get("segment_id"),
                    "label": segment.get("label"),
                    "speech_text": segment.get("speech_text"),
                    "frame_goal": segment.get("frame_goal"),
                    "timing_hint": segment.get("timing_hint"),
                    "audio_url": audio_item.get("audio_url"),
                }
            )
    answer_audio = None
    if not audio_segments:
        answer_audio = await safe_speak(sid, answer, speak_voice, sess["voice_lang"], sess.get("edge_voice"))
    append_history(sid, "assistant", answer)
    remember_teaching_loop(sess, request_ctx)
    if isinstance(answer_data.get("teaching_session_state"), dict):
        sess["teaching_session_state"] = answer_data["teaching_session_state"]
    sess["notes"].append({"q": question, "a": answer})
    if len(sess["notes"]) > MAX_NOTES:
        sess["notes"] = sess["notes"][-MAX_NOTES:]
    persist_session_data(sess)
    logger.info(
        "chat response sid=%s mode=video video_id=%s chars=%s has_audio=%s bridge=%s segments=%s",
        sid,
        video_id,
        len(answer),
        bool(answer_audio) or bool(audio_segments),
        bool(bridge),
        len(audio_segments),
    )

    return {
        "answer": answer,
        "follow_up": follow_up,
        "suggestions": suggestions,
        "timestamp": answer_data.get("timestamp"),
        "timestamps": timestamps,
        "source": source,
        "has_audio": bool(answer_audio) or bool(audio_segments),
        "board_actions": board_actions,
        "audio_url": answer_audio.get("audio_url") if answer_audio else None,
        "audio_segments": audio_segments,
        "reference_bridge": {
            **bridge,
            "intro_audio_url": bridge_audio_url,
        } if bridge else None,
        "visual_payload": visuals,
        "lesson_plan": lesson_plan,
        "teaching_segments": teaching_segments,
        "frame_sequence": frame_sequence,
        "pipeline_debug": answer_data.get("pipeline_debug"),
        "notes": sess["notes"],
        "transcript_log": sess["transcript_log"][-80:],
    }


async def stream_chat_events(payload: Dict[str, Any], background_tasks: BackgroundTasks):
    sid = payload.get("sid") or payload.get("session_id")
    lesson_id = clean_spaces(payload.get("lesson_id"))
    video_id = payload.get("video_id")
    question = clean_spaces(payload.get("question"))
    requested_voice_id = payload.get("voice_id") or payload.get("voice_name")
    trigger = payload.get("trigger")

    if lesson_id:
        response = await build_chat_response(payload)
        yield json_stream_line("plan", response)
        yield json_stream_line("done", {"notes": response.get("notes", []), "transcript_log": response.get("transcript_log", [])})
        return

    if not sid or not video_id or not question:
        yield json_stream_line("error", {"detail": "sid, video_id, and question are required"})
        return

    sess = get_session(sid)
    teaching_session_state = ensure_teaching_session_state(sess)
    video = find_video(video_id)
    if not video:
        yield json_stream_line("error", {"detail": "Video not found"})
        return
    if not stream_teaching_blueprint_async:
        response = await build_chat_response(payload)
        yield json_stream_line("plan", response)
        yield json_stream_line("done", {"notes": response.get("notes", []), "transcript_log": response.get("transcript_log", [])})
        return

    use_video_context = coerce_bool(payload.get("use_video_context"), sess.get("use_video_context", True))
    preferred_visualization = normalize_visualization_preference(
        payload.get("preferred_visualization"),
        sess.get("preferred_visualization", ""),
    )
    sess["preferred_visualization"] = preferred_visualization
    request_ctx = teaching_request_context(
        question,
        sess,
        source_mode="video",
        use_video_context=use_video_context,
        preferred_visualization=preferred_visualization,
    )
    sess["focus_video_id"] = video_id
    persist_session_data(sess)
    append_history(sid, "user", question)

    fallback = build_chat_payload(request_ctx["base_question"], video, sess) if use_video_context else build_non_video_chat_payload(request_ctx["base_question"], video)
    blueprint = None
    try:
        async for event in stream_teaching_blueprint_async(
            question=request_ctx["effective_question"],
            creator_name=video.get("creator_name", "the creator"),
            creator_profession=video.get("creator_profession", "educator"),
            video_title=video.get("title", "this video"),
            chunks_path=video.get("chunks_path") or f"video_{video_id}/chunks.json",
            conversation_history=sess["history"],
            trigger=trigger or None,
            use_video_context=use_video_context,
            pedagogy_mode=request_ctx["pedagogy_mode"],
            learner_request=request_ctx["learner_request"],
            topic_question=request_ctx["base_question"],
            preferred_visualization=request_ctx["preferred_visualization"],
            session_state=teaching_session_state,
        ):
            if event.get("event") == "first_text":
                yield json_stream_line("first_text", event.get("data") if isinstance(event.get("data"), dict) else {})
            elif event.get("event") == "blueprint" and isinstance(event.get("data"), dict):
                blueprint = event["data"]
    except Exception as exc:
        logger.exception("video stream blueprint failed sid=%s video_id=%s", sid, video_id)
        yield json_stream_line("error", {"detail": f"Teaching plan failed: {exc}"})
        return
    if not isinstance(blueprint, dict):
        yield json_stream_line("error", {"detail": "Teaching plan did not produce a blueprint"})
        return

    answer = clean_spaces(blueprint.get("answer")) or fallback["answer"]
    follow_up = clean_spaces(blueprint.get("follow_up")) or fallback["follow_up"]
    suggestions = [clean_spaces(item) for item in (blueprint.get("suggestions") or []) if clean_spaces(item)] or fallback["suggestions"]
    board_actions = build_pipeline_board_actions(blueprint) or fallback["board_actions"]
    bridge = blueprint.get("reference_bridge")
    if use_video_context and blueprint.get("timestamp") is not None and not bridge:
        ts = float(blueprint["timestamp"])
        bridge = {
            "start_sec": max(0.0, ts - 1.5),
            "end_sec": ts + 8.0,
            "label": "Relevant source moment",
            "intro_text": "I will show you the relevant moment in the video first. When it finishes, I will break it down on the board.",
        }

    remember_teaching_loop(sess, request_ctx)
    if isinstance(blueprint.get("teaching_session_state"), dict):
        sess["teaching_session_state"] = blueprint["teaching_session_state"]
    persist_session_data(sess)
    yield json_stream_line(
        "plan",
        {
            "answer": answer,
            "follow_up": follow_up,
            "suggestions": suggestions,
            "timestamp": blueprint.get("timestamp"),
            "timestamps": blueprint.get("timestamps", []),
            "source": blueprint.get("source", fallback.get("source", "classroom")),
            "board_actions": board_actions,
            "reference_bridge": bridge if use_video_context else None,
            "visual_payload": {"segments": []},
            "lesson_plan": blueprint.get("lesson_plan"),
            "teaching_segments": blueprint.get("teaching_segments", []),
            "frame_sequence": [],
            "audio_segments": [],
            "context_label": blueprint.get("context_label"),
            "pipeline_debug": blueprint.get("pipeline_debug"),
        },
    )

    speak_voice = requested_voice_id or sess["voice_id"]
    segments = [item for item in (blueprint.get("teaching_segments") or []) if isinstance(item, dict)]
    for index, segment in enumerate(segments, start=1):
        job_id = queue_segment_audio_job(
            background_tasks,
            session_id=sid,
            segment=segment,
            voice_id=speak_voice,
            lang=sess["voice_lang"],
            fallback_voice=sess.get("edge_voice"),
        )
        if not job_id:
            continue
        yield json_stream_line(
            "audio_pending",
            {
                "job_id": job_id,
                "segment_index": index,
                "segment_id": segment.get("segment_id"),
                "label": segment.get("label"),
                "speech_text": segment.get("speech_text"),
                "frame_goal": segment.get("frame_goal"),
                "timing_hint": segment.get("timing_hint"),
            },
        )

    prepared_task = None
    collected_frame_sequence: list[dict[str, Any]] = []

    for index, segment in enumerate(segments, start=1):
        if prepared_task is None:
            prepared_task = asyncio.create_task(
                prepare_streaming_segment_item(
                    question=request_ctx["base_question"] or request_ctx["effective_question"],
                    blueprint=blueprint,
                    segment=segment,
                    frame_number=index,
                )
            )
        current_task = prepared_task
        if index < len(segments):
            next_segment = segments[index]
            prepared_task = asyncio.create_task(
                prepare_streaming_segment_item(
                    question=request_ctx["base_question"] or request_ctx["effective_question"],
                    blueprint=blueprint,
                    segment=next_segment,
                    frame_number=index + 1,
                )
            )
        else:
            prepared_task = None
        prepared = await current_task
        if not prepared:
            continue
        if isinstance(prepared.get("frame_plan"), dict):
            collected_frame_sequence.append(prepared["frame_plan"])
        yield json_stream_line(
            "segment",
            {
                "segment_index": prepared.get("segment_index"),
                "segment": prepared.get("segment"),
                "visual_segment": prepared.get("visual_segment"),
                "frame_plan": prepared.get("frame_plan"),
            },
        )

    append_history(sid, "assistant", answer)
    sess["notes"].append({"q": question, "a": answer})
    if len(sess["notes"]) > MAX_NOTES:
        sess["notes"] = sess["notes"][-MAX_NOTES:]
    persist_session_data(sess)
    yield json_stream_line(
        "done",
        {
            "has_audio": bool(segments),
            "audio_segments": [],
            "frame_sequence": collected_frame_sequence,
            "pipeline_debug": blueprint.get("pipeline_debug"),
            "notes": sess["notes"],
            "transcript_log": sess["transcript_log"][-80:],
        },
    )

@app.post("/chat")
async def chat(request: Request):
    payload = await parse_request_payload(request)
    return await build_chat_response(payload)


@app.post("/chat-stream")
async def chat_stream(request: Request, background_tasks: BackgroundTasks):
    payload = await parse_request_payload(request)
    return StreamingResponse(stream_chat_events(payload, background_tasks), media_type="application/x-ndjson")

@app.post("/signal")
async def signal(request: Request):
    payload = await parse_request_payload(request)
    sid = payload.get("sid") or payload.get("session_id")
    video_id = payload.get("video_id")
    signal_name = payload.get("signal")
    if not sid or not video_id or not signal_name:
        raise HTTPException(status_code=400, detail="sid, video_id, and signal are required")
    question = SIGNAL_PROMPTS.get(signal_name, "Explain the current idea again.")
    return await build_chat_response(
        {
            "sid": sid,
            "video_id": video_id,
            "question": question,
            "voice_name": payload.get("voice_name"),
            "trigger": signal_name,
        }
    )
