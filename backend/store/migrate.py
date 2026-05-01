"""Idempotent seed/migration: lift the legacy `data/videos.json` flat list into
the new persona-centered model.

Rule: each unique creator_name in legacy videos becomes one teacher User +
one TeacherPersona, and every legacy video becomes a TeacherVideo linked to
that persona. The legacy file is left untouched so old code paths keep
working until phase 6 retires them.

Re-running is safe: existing rows (by `legacy_id` field on TeacherVideo, or
by email on User) are skipped.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import bcrypt

from config import DATA_DIR, VIDEOS_DB

from .models import PersonaPromptVersion, RoadmapPart, TeacherPersona, TeacherVideo, User, VideoRoadmap, utcnow
from .repository import (
    persona_prompts_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    users_repo,
    videos_repo,
)

logger = logging.getLogger("parallea.store.migrate")


def _slugify_email(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", ".", (name or "").lower()).strip(".")
    if not base:
        base = "teacher"
    return f"{base}@example.com"


def _legacy_videos() -> list[dict[str, Any]]:
    path: Path = VIDEOS_DB
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _ensure_seed_teacher(name: str, profession: str) -> tuple[dict[str, Any], dict[str, Any]]:
    email = _slugify_email(name)
    user = users_repo.first_where(email=email)
    if not user:
        user = users_repo.create(
            User(
                name=name or "Teacher",
                email=email,
                role="teacher",
                auth_provider="seed",
            )
        )
        logger.info("seed: created user %s (%s)", name, email)

    persona = personas_repo.first_where(teacher_id=user["id"])
    if not persona:
        persona = personas_repo.create(
            TeacherPersona(
                teacher_id=user["id"],
                teacher_name=name or "Teacher",
                profession=profession or "",
                style_summary="",
                avatar_preset_id="girl_1",
            )
        )
        logger.info("seed: created persona for %s", name)
    elif profession and persona.get("profession") != profession:
        personas_repo.update(persona["id"], {"profession": profession})
    return user, persona


def _ensure_video_link(video: dict[str, Any], persona: dict[str, Any], user: dict[str, Any]) -> None:
    legacy_id = video.get("id")
    if not legacy_id:
        return
    existing = videos_repo.first_where(id=legacy_id)
    if existing:
        return
    payload = TeacherVideo(
        id=legacy_id,
        teacher_id=user["id"],
        persona_id=persona["id"],
        title=video.get("title", ""),
        description=video.get("description", ""),
        subject=video.get("subject", ""),
        creator_name=video.get("creator_name") or video.get("creator", ""),
        creator_profession=video.get("creator_profession", ""),
        filename=video.get("filename", ""),
        thumbnail_url=video.get("thumbnail_url"),
        chunks_path=video.get("chunks_path"),
        has_transcript=bool(video.get("has_transcript", False)),
        status="ready" if video.get("has_transcript") else "uploaded",
        created_at=video.get("uploaded_at") or utcnow(),
    )
    videos_repo.create(payload)
    logger.info("seed: linked legacy video %s to persona %s", legacy_id, persona["id"])


DEMO_PASSWORD = "password123"
DEMO_TEACHER_ID = "usr_demo_guitar_teacher"
DEMO_STUDENT_ID = "usr_demo_student"
DEMO_PERSONA_ID = "per_demo_guitar_coach"
DEMO_VIDEO_ID = "vid_demo_guitar_lesson_1"
DEMO_ROADMAP_ID = "rmp_demo_guitar_lesson_1"


def _hash_seed_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode("utf-8")


def _dev_seed_enabled() -> bool:
    raw = os.getenv("PARALLEA_ENABLE_DEV_SEED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    env = os.getenv("PARALLEA_ENV", "").strip().lower()
    return env not in {"prod", "production"}


def _ensure_password_seed_user(*, user_id: str, name: str, email: str, role: str) -> dict[str, Any]:
    user = users_repo.get(user_id) or users_repo.first_where(email=email)
    fields = {
        "name": name,
        "email": email,
        "role": role,
        "auth_provider": "seed",
    }
    if user:
        update = {k: v for k, v in fields.items() if user.get(k) != v}
        if not user.get("password_hash"):
            update["password_hash"] = _hash_seed_password(DEMO_PASSWORD)
        if update:
            users_repo.update(user["id"], update)
            user = users_repo.get(user["id"])
        return user
    return users_repo.create(
        User(
            id=user_id,
            name=name,
            email=email,
            role=role,
            auth_provider="seed",
            password_hash=_hash_seed_password(DEMO_PASSWORD),
        )
    )


def _activate_seed_prompt(persona_id: str, prompt: str) -> None:
    for version in persona_prompts_repo.where(persona_id=persona_id):
        if version.get("is_active"):
            persona_prompts_repo.update(version["id"], {"is_active": False})
    existing = persona_prompts_repo.get("ver_demo_guitar_prompt_1")
    payload = PersonaPromptVersion(
        id="ver_demo_guitar_prompt_1",
        persona_id=persona_id,
        version=1,
        prompt=prompt,
        reason="dev_seed:guitar_lesson_1",
        is_active=True,
    ).to_dict()
    if existing:
        persona_prompts_repo.upsert(payload)
    else:
        persona_prompts_repo.create(payload)


def _ensure_guitar_demo_seed() -> dict[str, int]:
    if not _dev_seed_enabled():
        return {"users": 0, "personas": 0, "videos": 0, "roadmaps": 0, "parts": 0}

    counts = {"users": 0, "personas": 0, "videos": 0, "roadmaps": 0, "parts": 0}
    if not users_repo.get(DEMO_TEACHER_ID):
        counts["users"] += 1
    teacher = _ensure_password_seed_user(
        user_id=DEMO_TEACHER_ID,
        name="Guitar Coach",
        email="teacher@example.com",
        role="teacher",
    )
    if not users_repo.get(DEMO_STUDENT_ID):
        counts["users"] += 1
    _ensure_password_seed_user(
        user_id=DEMO_STUDENT_ID,
        name="Demo Student",
        email="student@example.com",
        role="student",
    )

    prompt = (
        "You teach as Guitar Coach, a Guitar Teacher. You are friendly, practical, and beginner-focused. "
        "You explain slowly and break every skill into small physical steps. You focus on chords, rhythm, "
        "finger placement, clean pressure near the frets, relaxed strumming, and the common mistakes beginners make. "
        "You ask the student to try short practice loops before moving ahead. You use concrete language about the "
        "neck, frets, strings, bridge, body, and hand position. When a student is confused, you slow down, name the "
        "exact finger or string, and give one correction at a time. You never rush into theory before the learner can "
        "feel the movement. You keep answers conversational and suitable for voice output. When a visual helps, ask "
        "for a simple guitar diagram, chord shape, or rhythm pattern animation."
    )
    style_summary = (
        "Friendly, practical, beginner-focused guitar teaching. Explains slowly with step-by-step practice around "
        "chords, rhythm, finger placement, and common beginner mistakes."
    )
    topics = [
        "guitar basics",
        "guitar parts",
        "finger placement",
        "basic chords",
        "switching chords",
        "strumming pattern",
        "practice routine",
    ]
    persona_payload = TeacherPersona(
        id=DEMO_PERSONA_ID,
        teacher_id=teacher["id"],
        teacher_name="Guitar Coach",
        profession="Guitar Teacher",
        active_persona_prompt=prompt,
        style_summary=style_summary,
        avatar_preset_id="man_2",
        voice_id="en-US-GuyNeural",
        detected_topics=topics,
    ).to_dict()
    if personas_repo.get(DEMO_PERSONA_ID):
        personas_repo.upsert(persona_payload)
    else:
        personas_repo.create(persona_payload)
        counts["personas"] += 1
    _activate_seed_prompt(DEMO_PERSONA_ID, prompt)

    transcript = (
        "Welcome to Lesson 1. First, choose the guitar that matches the one in your hands: steel acoustic, nylon "
        "acoustic, or electric. Then learn the main parts: bridge, neck, head, body, frets, and strings. Tune each "
        "string from low E to high E, listening for a steady center. Finish with three beginner chords: E minor, "
        "G major, and C major. Press just behind the frets, keep the thumb relaxed, strum slowly, and listen for "
        "clean ringing notes."
    )
    video_payload = TeacherVideo(
        id=DEMO_VIDEO_ID,
        teacher_id=teacher["id"],
        persona_id=DEMO_PERSONA_ID,
        title="First Guitar Lesson",
        description="Beginner lesson covering guitar types, parts, tuning, basic chords, and practice tips.",
        subject="Guitar",
        creator_name="Guitar Coach",
        creator_profession="Guitar Teacher",
        original_video_url=None,
        transcript=transcript,
        has_transcript=True,
        duration=720,
        status="ready",
        status_message="dev seed persona + roadmap ready",
        detected_topics=topics,
    ).to_dict()
    if videos_repo.get(DEMO_VIDEO_ID):
        videos_repo.upsert(video_payload)
    else:
        videos_repo.create(video_payload)
        counts["videos"] += 1

    roadmap_payload = VideoRoadmap(
        id=DEMO_ROADMAP_ID,
        video_id=DEMO_VIDEO_ID,
        persona_id=DEMO_PERSONA_ID,
        title="First Guitar Lesson Roadmap",
        summary="A beginner guitar path covering instrument basics, hand position, tuning, chords, strumming, and practice habits.",
        difficulty="beginner",
        topics=topics,
    ).to_dict()
    if roadmaps_repo.get(DEMO_ROADMAP_ID):
        roadmaps_repo.upsert(roadmap_payload)
    else:
        roadmaps_repo.create(roadmap_payload)
        counts["roadmaps"] += 1

    parts = [
        {
            "id": "prt_demo_guitar_1",
            "part_id": "part_1",
            "order": 0,
            "title": "Introduction to guitar basics",
            "start_time": 0,
            "end_time": 90,
            "summary": "Identify the type of guitar in the learner's hands and set a comfortable beginner mindset.",
            "transcript_chunk": "Choose the guitar that matches what is in your hands: steel acoustic, nylon acoustic, or electric.",
            "concepts": ["guitar basics", "steel acoustic", "nylon acoustic", "electric guitar"],
            "equations": [],
            "examples": ["Match the body shape and strings to your instrument."],
            "suggested_visuals": ["side-by-side guitar type comparison"],
        },
        {
            "id": "prt_demo_guitar_2",
            "part_id": "part_2",
            "order": 1,
            "title": "Holding the guitar and hand position",
            "start_time": 90,
            "end_time": 180,
            "summary": "Build a relaxed posture and learn where the fretting hand and strumming hand should sit.",
            "transcript_chunk": "Keep your thumb relaxed behind the neck so your fingers can curve cleanly over the strings.",
            "concepts": ["hand position", "thumb behind neck", "curved fingers", "relaxed strumming hand"],
            "equations": [],
            "examples": ["Rest the guitar body steadily before pressing any chord."],
            "suggested_visuals": ["guitar posture and hand placement diagram"],
        },
        {
            "id": "prt_demo_guitar_3",
            "part_id": "part_3",
            "order": 2,
            "title": "Basic chords",
            "start_time": 180,
            "end_time": 330,
            "summary": "Learn the first open chord shapes: E minor, G major, and C major.",
            "transcript_chunk": "E minor uses two fingers. G major uses three fingers. C major teaches spacing and clean string control.",
            "concepts": ["basic chords", "E minor", "G major", "C major", "open chords"],
            "equations": [],
            "examples": ["Place finger 2 and finger 3 on the second fret for E minor."],
            "suggested_visuals": ["open chord diagrams for Em, G, and C"],
        },
        {
            "id": "prt_demo_guitar_4",
            "part_id": "part_4",
            "order": 3,
            "title": "Switching chords",
            "start_time": 330,
            "end_time": 450,
            "summary": "Practice slow chord changes without rushing the strumming hand.",
            "transcript_chunk": "Move one finger at a time first, then try switching between shapes while keeping the hand relaxed.",
            "concepts": ["switching chords", "slow transitions", "finger memory"],
            "equations": [],
            "examples": ["Switch Em to G slowly before adding rhythm."],
            "suggested_visuals": ["step-by-step chord transition path"],
        },
        {
            "id": "prt_demo_guitar_5",
            "part_id": "part_5",
            "order": 4,
            "title": "Strumming pattern",
            "start_time": 450,
            "end_time": 570,
            "summary": "Add a simple downstroke rhythm after the fretting hand feels stable.",
            "transcript_chunk": "Start with slow downstrokes, keep the wrist loose, and listen for every string to ring clearly.",
            "concepts": ["strumming pattern", "downstrokes", "rhythm", "loose wrist"],
            "equations": [],
            "examples": ["Four slow downstrokes per chord."],
            "suggested_visuals": ["animated downstroke rhythm pattern"],
        },
        {
            "id": "prt_demo_guitar_6",
            "part_id": "part_6",
            "order": 5,
            "title": "Practice tips",
            "start_time": 570,
            "end_time": 720,
            "summary": "Use a short routine to avoid buzzing notes and build clean beginner habits.",
            "transcript_chunk": "Press close to the fret wire, check one string at a time, and practice in short loops.",
            "concepts": ["practice routine", "beginner mistakes", "buzzing notes", "clean chord sound"],
            "equations": [],
            "examples": ["Play each chord once, fix buzzing strings, then repeat for two minutes."],
            "suggested_visuals": ["practice checklist"],
        },
    ]
    for raw in parts:
        payload = RoadmapPart(roadmap_id=DEMO_ROADMAP_ID, **raw).to_dict()
        if roadmap_parts_repo.get(payload["id"]):
            roadmap_parts_repo.upsert(payload)
        else:
            roadmap_parts_repo.create(payload)
            counts["parts"] += 1
    return counts


def run_migration() -> dict[str, int]:
    """Run the migration. Safe to call repeatedly."""
    legacy = _legacy_videos()
    legacy.sort(key=lambda v: v.get("uploaded_at") or "")  # latest profession wins
    by_creator: dict[str, dict[str, Any]] = {}
    for video in legacy:
        creator = (video.get("creator_name") or video.get("creator") or "").strip()
        if not creator:
            continue
        by_creator[creator] = {
            "profession": (video.get("creator_profession") or "").strip()
            or by_creator.get(creator, {}).get("profession", ""),
        }

    counts = {"users": 0, "personas": 0, "videos": 0}
    creator_to_persona: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for name, info in by_creator.items():
        user_before = users_repo.first_where(email=_slugify_email(name))
        persona_before = (
            personas_repo.first_where(teacher_id=user_before["id"]) if user_before else None
        )
        user, persona = _ensure_seed_teacher(name, info["profession"])
        if not user_before:
            counts["users"] += 1
        if not persona_before:
            counts["personas"] += 1
        creator_to_persona[name] = (user, persona)

    for video in legacy:
        creator = (video.get("creator_name") or video.get("creator") or "").strip()
        if not creator or creator not in creator_to_persona:
            continue
        before = videos_repo.first_where(id=video.get("id"))
        user, persona = creator_to_persona[creator]
        _ensure_video_link(video, persona, user)
        if not before:
            counts["videos"] += 1

    demo_counts = _ensure_guitar_demo_seed()
    for key, value in demo_counts.items():
        counts[key] = counts.get(key, 0) + value
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = run_migration()
    print(f"seed complete: {result}")
    print(f"data dir: {DATA_DIR}")
