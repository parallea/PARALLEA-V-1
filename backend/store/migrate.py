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
import re
from pathlib import Path
from typing import Any

from config import DATA_DIR, VIDEOS_DB

from .models import TeacherPersona, TeacherVideo, User, utcnow
from .repository import (
    personas_repo,
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

    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = run_migration()
    print(f"seed complete: {result}")
    print(f"data dir: {DATA_DIR}")
