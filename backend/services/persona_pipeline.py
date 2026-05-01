"""Teacher persona + video roadmap pipeline.

Each teacher has exactly one evolving `TeacherPersona`:

  first video → generate_initial_persona_and_roadmap()
                  → personaPrompt + styleSummary + detectedTopics + videoRoadmap

  Nth video  → update_persona_and_create_roadmap()
                  → updatedPersonaPrompt + updateSummary + newTopicsDetected + videoRoadmap

`process_teacher_video(video_id)` orchestrates: status transitions,
transcribe → load existing prompt → call the right LLM service → persist
prompt-version + roadmap + roadmap parts. Idempotent on success.

LLM provider preference (auto-detected):
  1. OpenAI (PARALLEA_OPENAI_PIPELINE_MODEL, OPENAI_API_KEY) — JSON object response_format
  2. Gemini (PARALLEA_GEMINI_TEACHING_MODEL, GEMINI_API_KEY)
  3. Stub mode — deterministic placeholder JSON (`PARALLEA_PERSONA_PIPELINE_MODE=stub`
     or no provider configured). Lets the rest of the platform run offline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from config import (
    DATA_DIR,
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    PARALLEA_OPENAI_PIPELINE_MODEL,
    UPLOADS_DIR,
)
from backend.store import (
    persona_prompts_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    videos_repo,
)
from backend.store.models import (
    PersonaPromptVersion,
    RoadmapPart,
    TeacherPersona,
    VideoRoadmap,
    utcnow,
)
from backend.services.model_router import llm_json as routed_llm_json
from backend.services.storage_service import url_for_object
from transcribe import save_chunks, transcribe_with_timestamps

logger = logging.getLogger("parallea.persona_pipeline")

# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------


def _stub_mode() -> bool:
    return (os.getenv("PARALLEA_PERSONA_PIPELINE_MODE") or "").strip().lower() == "stub"


def _provider() -> str:
    """Pick the first available LLM provider, or 'stub' for offline."""
    if _stub_mode():
        return "stub"
    if OPENAI_API_KEY:
        try:
            from openai import AsyncOpenAI  # noqa: F401
            return "openai"
        except Exception:  # noqa: BLE001
            pass
    if GEMINI_API_KEY:
        return "gemini"
    return "stub"


def _openai_model_family(model: str) -> str:
    return (model or "").strip().lower()


def _openai_uses_completion_tokens(model: str) -> bool:
    family = _openai_model_family(model)
    return family.startswith(("gpt-5", "o1", "o3", "o4"))


def _openai_supports_temperature(model: str) -> bool:
    # Newer reasoning models reject non-default temperature in Chat Completions.
    return not _openai_uses_completion_tokens(model)


def _openai_chat_options(model: str, *, max_tokens: int, temperature: float) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if _openai_uses_completion_tokens(model):
        options["max_completion_tokens"] = max_tokens
    else:
        options["max_tokens"] = max_tokens
    if _openai_supports_temperature(model):
        options["temperature"] = temperature
    return options


async def _call_openai_json(system: str, user: str, *, max_tokens: int = 4500, temperature: float = 0.3) -> str:
    from openai import AsyncOpenAI  # late import keeps the module importable without the package

    model = PARALLEA_OPENAI_PIPELINE_MODEL
    options = _openai_chat_options(model, max_tokens=max_tokens, temperature=temperature)
    logger.info(
        "persona pipeline openai request provider=openai model=%s token_param=%s temperature_passed=%s",
        model,
        "max_completion_tokens" if "max_completion_tokens" in options else "max_tokens",
        "temperature" in options,
    )
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        **options,
    )
    return resp.choices[0].message.content or ""


async def _call_gemini_json(system: str, user: str, *, max_tokens: int = 4500, temperature: float = 0.3) -> str:
    from gemini_service import build_gemini_client, generate_json_with_retry

    client = build_gemini_client(GEMINI_API_KEY, enabled=bool(GEMINI_API_KEY))
    if not client:
        raise RuntimeError("Gemini provider unavailable")
    model = os.getenv("PARALLEA_GEMINI_TEACHING_MODEL", "gemini-2.5-flash")
    logger.info("persona pipeline gemini request provider=gemini model=%s", model)
    return await generate_json_with_retry(
        client,
        model=model,
        prompt=user,
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
        operation="persona-pipeline",
    )


async def llm_json(system: str, user: str, *, max_tokens: int = 4500, temperature: float = 0.3) -> dict[str, Any]:
    return await llm_json_task("persona", system, user, max_tokens=max_tokens, temperature=temperature)


async def llm_json_task(task: str, system: str, user: str, *, max_tokens: int = 4500, temperature: float = 0.3) -> dict[str, Any]:
    try:
        payload = await routed_llm_json(task, system, user, max_tokens=max_tokens, temperature=temperature)
        return payload or _stub_payload(system, user)
    except Exception as exc:  # noqa: BLE001
        logger.exception("persona pipeline routed LLM call failed task=%s: %s; falling back to stub", task, exc)
        return _stub_payload(system, user)


async def _legacy_llm_json(system: str, user: str, *, max_tokens: int = 4500, temperature: float = 0.3) -> dict[str, Any]:
    provider = _provider()
    model = ""
    if provider == "openai":
        model = PARALLEA_OPENAI_PIPELINE_MODEL
    elif provider == "gemini":
        model = os.getenv("PARALLEA_GEMINI_TEACHING_MODEL", "gemini-2.5-flash")
    logger.info("persona pipeline using provider=%s model=%s", provider, model or "stub")
    if provider == "stub":
        return _stub_payload(system, user)
    raw = ""
    try:
        if provider == "openai":
            raw = await _call_openai_json(system, user, max_tokens=max_tokens, temperature=temperature)
        else:
            raw = await _call_gemini_json(system, user, max_tokens=max_tokens, temperature=temperature)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM call failed (%s): %s — falling back to stub", provider, exc)
        return _stub_payload(system, user)
    return _safe_json_loads(raw)


def _safe_json_loads(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    raw = raw.strip()
    # Strip ```json fences if present.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Last-ditch: extract the first {...} block.
        first = raw.find("{")
        last = raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first : last + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("could not parse LLM JSON, raw head: %s", raw[:200])
        return {}


def _stub_payload(system: str, user: str) -> dict[str, Any]:  # noqa: ARG001
    """Deterministic placeholder so phases 4-6 work offline.

    The shape mirrors both the initial and update schemas; consumers pick
    whichever fields apply. Topics/parts are derived from the user prompt
    when possible.
    """
    title = "Untitled lesson"
    teacher_name = "Teacher"
    profession = ""
    transcript_excerpt = ""
    for line in user.splitlines():
        if line.startswith("VIDEO_TITLE:"):
            title = line.split(":", 1)[1].strip() or title
        elif line.startswith("TEACHER_NAME:"):
            teacher_name = line.split(":", 1)[1].strip() or teacher_name
        elif line.startswith("TEACHER_PROFESSION:"):
            profession = line.split(":", 1)[1].strip()
        elif line.startswith("TRANSCRIPT:"):
            transcript_excerpt = line.split(":", 1)[1].strip()[:400]

    persona = (
        f"You teach as {teacher_name}, a {profession or 'subject expert'}. "
        f"Your style is conversational, structured, and step-by-step, building from basics to "
        f"applied examples. You confirm understanding before moving on, restate the goal "
        f"of every part, and prefer short, vivid analogies over jargon. Keep responses "
        f"compact, suited to voice, and pause to invite questions at natural breakpoints."
    )
    summary = f"{teacher_name} teaches {title.lower() or 'this topic'} clearly and patiently."
    topics = [t.strip() for t in title.replace(",", " ").split() if len(t.strip()) > 3][:4] or ["foundations"]
    parts = [
        {
            "partId": "part_1",
            "title": f"Introduction to {title}",
            "startTime": 0,
            "endTime": 120,
            "summary": "Set up the core question and why it matters.",
            "transcriptChunk": transcript_excerpt or "(transcript stub)",
            "concepts": topics[:3],
            "equations": [],
            "examples": [],
            "suggestedVisuals": ["concept map"],
        }
    ]
    roadmap = {
        "videoTitle": title,
        "summary": summary,
        "difficulty": "beginner",
        "topics": topics,
        "parts": parts,
    }
    return {
        "personaPrompt": persona,
        "updatedPersonaPrompt": persona,
        "styleSummary": summary,
        "updateSummary": "Stub generated; configure OPENAI_API_KEY or GEMINI_API_KEY for a real persona.",
        "detectedTopics": topics,
        "newTopicsDetected": topics,
        "videoRoadmap": roadmap,
    }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


_SYSTEM_INITIAL = """You are designing the FIRST persona for a teacher who just uploaded their first lesson video.

Read the transcript and metadata. Build:
  1) personaPrompt — a reusable system prompt that captures THIS teacher's tone, explanation style, examples, pacing, strengths, subject expertise, and student interaction style. 12+ sentences, written in the second person ("You teach as ..."). It must be reusable across topics, not tied to this single video.
  2) styleSummary — a short one-paragraph public-facing summary (2-3 sentences).
  3) detectedTopics — 3 to 8 short topic strings present in the video.
  4) videoRoadmap — divide the transcript into 3-7 ordered teaching parts. Each part has:
       partId ("part_1", "part_2", ...), title, startTime, endTime, summary,
       transcriptChunk (verbatim or near-verbatim slice), concepts[], equations[],
       examples[], suggestedVisuals[].

Return STRICT JSON ONLY, no markdown fences, exactly this shape:
{
  "personaPrompt": "string",
  "styleSummary": "string",
  "detectedTopics": ["string"],
  "videoRoadmap": {
    "videoTitle": "string",
    "summary": "string",
    "difficulty": "beginner | intermediate | advanced",
    "topics": ["string"],
    "parts": [{"partId":"part_1","title":"string","startTime":0,"endTime":120,"summary":"string","transcriptChunk":"string","concepts":["string"],"equations":["string"],"examples":["string"],"suggestedVisuals":["string"]}]
  }
}"""


_SYSTEM_UPDATE = """You are evolving an EXISTING teacher persona based on a new lesson video the same teacher uploaded.

You are given the current personaPrompt and the new transcript + metadata. Decide:
  1) updatedPersonaPrompt — refined persona that incorporates new patterns (subjects, examples, pacing) observed in this video, while preserving everything still true. Same structure as before. Do NOT create a different persona; evolve this one.
  2) updateSummary — what changed in the persona prompt and why (1-2 sentences).
  3) newTopicsDetected — topics from this video that the teacher didn't have before.
  4) videoRoadmap — same shape as the initial roadmap.

Return STRICT JSON ONLY, no markdown fences, exactly this shape:
{
  "updatedPersonaPrompt": "string",
  "updateSummary": "string",
  "newTopicsDetected": ["string"],
  "videoRoadmap": {
    "videoTitle": "string",
    "summary": "string",
    "difficulty": "beginner | intermediate | advanced",
    "topics": ["string"],
    "parts": [{"partId":"part_1","title":"string","startTime":0,"endTime":120,"summary":"string","transcriptChunk":"string","concepts":["string"],"equations":["string"],"examples":["string"],"suggestedVisuals":["string"]}]
  }
}"""


_SYSTEM_INITIAL_PERSONA_ONLY = """You are generating the reusable teacher persona prompt for the first uploaded lesson video.

Use the transcript and metadata to capture the teacher's tone, pacing, explanation style, examples, strengths, subject expertise, and student interaction style.
Return STRICT JSON ONLY, no markdown fences, exactly this shape:
{
  "personaPrompt": "12+ sentence second-person reusable system prompt",
  "styleSummary": "2-3 sentence public-facing summary",
  "detectedTopics": ["topic"]
}"""


_SYSTEM_UPDATE_PERSONA_ONLY = """You are updating an existing reusable teacher persona prompt from a newly uploaded lesson video.

Preserve the same teacher persona and refine only what the new transcript supports.
Return STRICT JSON ONLY, no markdown fences, exactly this shape:
{
  "updatedPersonaPrompt": "12+ sentence refined second-person reusable system prompt",
  "updateSummary": "1-2 sentence summary of what changed",
  "newTopicsDetected": ["topic"]
}"""


_SYSTEM_ROADMAP_ONLY = """You are generating the video roadmap for a teacher-uploaded lesson video.

Use the transcript timestamps as the source of truth. Divide the lesson into 3-7 ordered parts that can be played as original video segments in an immersive lesson.
Return STRICT JSON ONLY, no markdown fences, exactly this shape:
{
  "videoRoadmap": {
    "videoTitle": "string",
    "summary": "string",
    "difficulty": "beginner | intermediate | advanced",
    "topics": ["string"],
    "parts": [{"partId":"part_1","title":"string","startTime":0,"endTime":120,"summary":"string","transcriptChunk":"string","concepts":["string"],"equations":["string"],"examples":["string"],"suggestedVisuals":["string"]}]
  }
}"""


def _build_user_prompt(*, video_title: str, teacher_name: str, profession: str, subject: str, description: str, transcript_with_timestamps: str, existing_persona_prompt: Optional[str] = None) -> str:
    parts = [
        f"VIDEO_TITLE: {video_title or 'Untitled'}",
        f"TEACHER_NAME: {teacher_name or 'Teacher'}",
        f"TEACHER_PROFESSION: {profession or ''}",
        f"SUBJECT: {subject or ''}",
        f"DESCRIPTION: {description or ''}",
    ]
    if existing_persona_prompt:
        parts.append("EXISTING_PERSONA_PROMPT_BEGIN")
        parts.append(existing_persona_prompt)
        parts.append("EXISTING_PERSONA_PROMPT_END")
    parts.append("TRANSCRIPT_BEGIN")
    parts.append(transcript_with_timestamps)
    parts.append("TRANSCRIPT_END")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------


def _set_status(video_id: str, status: str, message: Optional[str] = None) -> None:
    fields: dict[str, Any] = {"status": status}
    if message is not None:
        fields["status_message"] = message
    videos_repo.update(video_id, fields)
    logger.info("video %s status=%s msg=%s", video_id, status, message or "")


def _video_root_dir(video_id: str) -> Path:
    return DATA_DIR / f"video_{video_id}"


def _resolve_video_file(video: dict[str, Any]) -> Optional[Path]:
    filename = (video.get("filename") or "").strip()
    if not filename:
        return None
    candidate = UPLOADS_DIR / filename
    if candidate.exists():
        return candidate
    candidate = Path(filename)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    return None


def _resolve_video_transcription_source(video: dict[str, Any]) -> Optional[str]:
    if video.get("storage_backend") == "s3" and video.get("object_key"):
        return url_for_object(str(video["object_key"]))
    local_file = _resolve_video_file(video)
    return str(local_file) if local_file else None


def _chunks_relative_path(video_id: str) -> str:
    return f"video_{video_id}/chunks.json"


def _load_chunks_for_video(video: dict[str, Any]) -> Optional[dict[str, Any]]:
    rel = video.get("chunks_path") or _chunks_relative_path(video["id"])
    abs_path = DATA_DIR / rel
    if not abs_path.exists():
        return None
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return {int(k): v for k, v in data.items()} if isinstance(data, dict) else None


def transcribe_teacher_video(video_id: str) -> dict[str, Any]:
    """Transcribe the video file (if not already transcribed) and persist chunks.

    Returns the updated video row.
    """
    video = videos_repo.get(video_id)
    if not video:
        raise ValueError(f"video {video_id} not found")
    if video.get("has_transcript") and _load_chunks_for_video(video):
        return video
    src = _resolve_video_transcription_source(video)
    if not src:
        _set_status(video_id, "failed", "video source not found")
        raise FileNotFoundError(f"video file missing: {video.get('filename')}")
    _set_status(video_id, "transcribing", "extracting audio + running ASR")
    chunks = transcribe_with_timestamps(str(src))
    rel = _chunks_relative_path(video_id)
    save_chunks(chunks, str(DATA_DIR / rel))
    transcript_text = "\n".join(
        f"[{int(c['start_sec'])}s-{int(c['end_sec'])}s] {c['text']}"
        for _, c in sorted(chunks.items(), key=lambda kv: int(kv[0]))
    )
    videos_repo.update(
        video_id,
        {
            "has_transcript": True,
            "chunks_path": rel,
            "transcript": transcript_text,
            "duration": max((c.get("end_sec") or 0) for c in chunks.values()) if chunks else None,
        },
    )
    return videos_repo.get(video_id)


def _format_transcript_for_prompt(video: dict[str, Any]) -> str:
    if video.get("transcript"):
        return str(video["transcript"])
    chunks = _load_chunks_for_video(video) or {}
    return "\n".join(
        f"[{int(c['start_sec'])}s-{int(c['end_sec'])}s] {c['text']}"
        for _, c in sorted(chunks.items(), key=lambda kv: int(kv[0]))
    )


# ---------------------------------------------------------------------------
# LLM-backed services
# ---------------------------------------------------------------------------


async def generate_initial_persona_and_roadmap(
    *,
    transcript: str,
    video_title: str,
    teacher_name: str,
    profession: str,
    subject: str = "",
    description: str = "",
) -> dict[str, Any]:
    user = _build_user_prompt(
        video_title=video_title,
        teacher_name=teacher_name,
        profession=profession,
        subject=subject,
        description=description,
        transcript_with_timestamps=transcript,
    )
    persona_payload = await llm_json_task("persona", _SYSTEM_INITIAL_PERSONA_ONLY, user, max_tokens=3200, temperature=0.25)
    roadmap_payload = await llm_json_task("roadmap", _SYSTEM_ROADMAP_ONLY, user, max_tokens=4200, temperature=0.25)
    roadmap = roadmap_payload.get("videoRoadmap") if isinstance(roadmap_payload, dict) else {}
    if not isinstance(roadmap, dict):
        roadmap = {}
    return {
        "personaPrompt": (persona_payload.get("personaPrompt") or "").strip(),
        "styleSummary": (persona_payload.get("styleSummary") or "").strip(),
        "detectedTopics": list(persona_payload.get("detectedTopics") or roadmap.get("topics") or []),
        "videoRoadmap": roadmap,
    }


async def update_persona_and_create_roadmap(
    *,
    existing_persona_prompt: str,
    transcript: str,
    video_title: str,
    teacher_name: str,
    profession: str,
    subject: str = "",
    description: str = "",
) -> dict[str, Any]:
    user = _build_user_prompt(
        video_title=video_title,
        teacher_name=teacher_name,
        profession=profession,
        subject=subject,
        description=description,
        transcript_with_timestamps=transcript,
        existing_persona_prompt=existing_persona_prompt,
    )
    persona_payload = await llm_json_task("persona", _SYSTEM_UPDATE_PERSONA_ONLY, user, max_tokens=3200, temperature=0.25)
    roadmap_payload = await llm_json_task("roadmap", _SYSTEM_ROADMAP_ONLY, user, max_tokens=4200, temperature=0.25)
    roadmap = roadmap_payload.get("videoRoadmap") if isinstance(roadmap_payload, dict) else {}
    if not isinstance(roadmap, dict):
        roadmap = {}
    return {
        "updatedPersonaPrompt": (persona_payload.get("updatedPersonaPrompt") or persona_payload.get("personaPrompt") or "").strip(),
        "updateSummary": (persona_payload.get("updateSummary") or "").strip(),
        "newTopicsDetected": list(persona_payload.get("newTopicsDetected") or persona_payload.get("detectedTopics") or roadmap.get("topics") or []),
        "videoRoadmap": roadmap,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _activate_persona_prompt(persona_id: str, prompt_text: str, reason: str) -> dict[str, Any]:
    versions = persona_prompts_repo.where(persona_id=persona_id)
    next_version = max((v.get("version") or 0 for v in versions), default=0) + 1
    for v in versions:
        if v.get("is_active"):
            persona_prompts_repo.update(v["id"], {"is_active": False})
    new_version = persona_prompts_repo.create(
        PersonaPromptVersion(
            persona_id=persona_id,
            version=next_version,
            prompt=prompt_text,
            reason=reason,
            is_active=True,
        )
    )
    personas_repo.update(persona_id, {"active_persona_prompt": prompt_text})
    return new_version


def _save_roadmap(persona_id: str, video_id: str, roadmap_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(roadmap_payload, dict):
        roadmap_payload = {}
    # Remove any existing roadmap for this video so re-processing is idempotent.
    for existing in roadmaps_repo.where(video_id=video_id):
        for part in roadmap_parts_repo.where(roadmap_id=existing["id"]):
            roadmap_parts_repo.delete(part["id"])
        roadmaps_repo.delete(existing["id"])

    roadmap = roadmaps_repo.create(
        VideoRoadmap(
            video_id=video_id,
            persona_id=persona_id,
            title=str(roadmap_payload.get("videoTitle") or "")[:300],
            summary=str(roadmap_payload.get("summary") or "")[:2000],
            difficulty=str(roadmap_payload.get("difficulty") or "beginner"),
            topics=list(roadmap_payload.get("topics") or []),
        )
    )
    parts_payload = roadmap_payload.get("parts") or []
    for idx, raw_part in enumerate(parts_payload):
        if not isinstance(raw_part, dict):
            continue
        roadmap_parts_repo.create(
            RoadmapPart(
                roadmap_id=roadmap["id"],
                part_id=str(raw_part.get("partId") or f"part_{idx + 1}"),
                order=idx,
                title=str(raw_part.get("title") or "")[:200],
                start_time=float(raw_part.get("startTime") or 0),
                end_time=float(raw_part.get("endTime") or 0),
                transcript_chunk=str(raw_part.get("transcriptChunk") or "")[:8000],
                summary=str(raw_part.get("summary") or "")[:2000],
                concepts=list(raw_part.get("concepts") or []),
                equations=list(raw_part.get("equations") or []),
                examples=list(raw_part.get("examples") or []),
                suggested_visuals=list(raw_part.get("suggestedVisuals") or []),
            )
        )
    return roadmap


def _merge_topics(persona: dict[str, Any], new_topics: list[str]) -> None:
    current = list(persona.get("detected_topics") or [])
    seen = {t.lower() for t in current if isinstance(t, str)}
    for t in new_topics or []:
        if not isinstance(t, str):
            continue
        norm = t.strip()
        if not norm or norm.lower() in seen:
            continue
        current.append(norm)
        seen.add(norm.lower())
    personas_repo.update(persona["id"], {"detected_topics": current})


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


async def process_teacher_video(video_id: str) -> dict[str, Any]:
    """End-to-end: transcribe → persona/roadmap → persist.

    Marks the video status throughout. Returns a dict summary.
    """
    video = videos_repo.get(video_id)
    if not video:
        raise ValueError(f"video {video_id} not found")
    persona = personas_repo.get(video.get("persona_id") or "")
    if not persona:
        raise ValueError(f"persona {video.get('persona_id')} not found for video {video_id}")

    try:
        # 1) transcribe (skip if cached)
        if not video.get("has_transcript") or not _load_chunks_for_video(video):
            video = transcribe_teacher_video(video_id)
        else:
            logger.info("video %s already transcribed; skipping ASR", video_id)

        # 2) prep prompt material
        transcript = _format_transcript_for_prompt(video)
        if not transcript.strip():
            _set_status(video_id, "failed", "transcript empty")
            return {"ok": False, "error": "transcript empty"}

        # 3) call the right LLM service
        existing_prompt = persona.get("active_persona_prompt") or ""
        is_first = not existing_prompt.strip()
        _set_status(video_id, "analyzing", "first-time persona generation" if is_first else "updating persona")

        if is_first:
            payload = await generate_initial_persona_and_roadmap(
                transcript=transcript,
                video_title=video.get("title", ""),
                teacher_name=persona.get("teacher_name") or video.get("creator_name", ""),
                profession=persona.get("profession") or video.get("creator_profession", ""),
                subject=video.get("subject", ""),
                description=video.get("description", ""),
            )
            prompt_text = (payload.get("personaPrompt") or "").strip()
            style_summary = (payload.get("styleSummary") or "").strip()
            new_topics = list(payload.get("detectedTopics") or [])
            roadmap_payload = payload.get("videoRoadmap") or {}
            reason = "initial"
        else:
            payload = await update_persona_and_create_roadmap(
                existing_persona_prompt=existing_prompt,
                transcript=transcript,
                video_title=video.get("title", ""),
                teacher_name=persona.get("teacher_name") or video.get("creator_name", ""),
                profession=persona.get("profession") or video.get("creator_profession", ""),
                subject=video.get("subject", ""),
                description=video.get("description", ""),
            )
            prompt_text = (payload.get("updatedPersonaPrompt") or "").strip()
            style_summary = persona.get("style_summary") or ""
            new_topics = list(payload.get("newTopicsDetected") or [])
            roadmap_payload = payload.get("videoRoadmap") or {}
            reason = f"update_from_video:{video_id}"

        if not prompt_text:
            _set_status(video_id, "failed", "LLM returned empty persona prompt")
            return {"ok": False, "error": "empty persona prompt"}

        # 4) persist persona + roadmap atomically (best-effort; in JSON store it's separate writes)
        _set_status(video_id, "generating", "saving persona prompt + roadmap")
        version = _activate_persona_prompt(persona["id"], prompt_text, reason)
        if style_summary:
            personas_repo.update(persona["id"], {"style_summary": style_summary})
        _merge_topics(personas_repo.get(persona["id"]), new_topics)
        roadmap = _save_roadmap(persona["id"], video_id, roadmap_payload)

        # 5) finalize
        videos_repo.update(
            video_id,
            {
                "detected_topics": list(roadmap_payload.get("topics") or []),
                "duration": video.get("duration"),
            },
        )
        _set_status(video_id, "ready", "persona + roadmap ready")

        return {
            "ok": True,
            "video_id": video_id,
            "persona_id": persona["id"],
            "prompt_version": version["version"],
            "roadmap_id": roadmap["id"],
            "parts": len(roadmap_payload.get("parts") or []),
            "first_video": is_first,
            "provider": _provider(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_teacher_video failed: %s", exc)
        _set_status(video_id, "failed", str(exc))
        return {"ok": False, "error": str(exc)}


def process_teacher_video_sync(video_id: str) -> dict[str, Any]:
    """Sync wrapper for CLI / FastAPI BackgroundTasks."""
    try:
        return asyncio.run(process_teacher_video(video_id))
    except RuntimeError:
        # already in an event loop (rare in our callers, but be defensive)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(process_teacher_video(video_id))
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Process a teacher video into persona+roadmap.")
    parser.add_argument("video_id", help="Video id (e.g. 5e3093c9)")
    parser.add_argument("--transcribe-only", action="store_true", help="Only transcribe, skip LLM step")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.transcribe_only:
        video = transcribe_teacher_video(args.video_id)
        print(json.dumps({"ok": True, "video_id": video["id"], "has_transcript": video["has_transcript"]}, indent=2))
        return

    result = process_teacher_video_sync(args.video_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
