"""Canonical entity shapes for the persona-centered platform.

Dataclasses are convenience wrappers — the source of truth on disk is JSON.
Each model exposes `to_dict()` / `from_dict()` so callers can move freely
between dict and typed forms.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}_{raw}" if prefix else raw


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class User:
    id: str = field(default_factory=lambda: new_id("usr"))
    name: str = ""
    email: str = ""
    image: Optional[str] = None
    role: str = "student"  # "teacher" | "student" | "admin"
    auth_provider: str = "email"  # "google" | "email" | "seed"
    password_hash: Optional[str] = None
    google_sub: Optional[str] = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class TeacherPersona:
    id: str = field(default_factory=lambda: new_id("per"))
    teacher_id: str = ""  # User.id
    teacher_name: str = ""
    profession: str = ""
    active_persona_prompt: Optional[str] = None
    style_summary: str = ""
    avatar_image_url: Optional[str] = None
    avatar_preset_id: Optional[str] = None  # legacy avatar (Ava/Mia/etc.)
    voice_id: Optional[str] = None
    supported_languages: list[str] = field(default_factory=lambda: ["en"])
    detected_topics: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeacherPersona":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        if payload.get("supported_languages") is None:
            payload["supported_languages"] = ["en"]
        if payload.get("detected_topics") is None:
            payload["detected_topics"] = []
        return cls(**payload)


@dataclass
class PersonaPromptVersion:
    id: str = field(default_factory=lambda: new_id("ver"))
    persona_id: str = ""
    version: int = 1
    prompt: str = ""
    reason: str = ""  # "initial" | "update_from_video:<videoId>" | "manual_edit"
    is_active: bool = False
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaPromptVersion":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class TeacherVideo:
    id: str = field(default_factory=lambda: new_id("vid"))
    teacher_id: str = ""
    persona_id: str = ""
    title: str = ""
    description: str = ""
    subject: str = ""
    creator_name: str = ""
    creator_profession: str = ""
    filename: str = ""
    original_video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    storage_backend: str = "local"
    object_key: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    thumbnail_object_key: Optional[str] = None
    thumbnail_storage_backend: Optional[str] = None
    thumbnail_storage_url: Optional[str] = None
    thumbnail_content_type: Optional[str] = None
    thumbnail_size_bytes: Optional[int] = None
    transcript: Optional[str] = None  # full transcript text (chunks live elsewhere)
    chunks_path: Optional[str] = None  # relative path under DATA_DIR
    has_transcript: bool = False
    duration: Optional[float] = None
    status: str = "uploaded"  # uploading|transcribing|analyzing|generating|ready|failed
    status_message: Optional[str] = None
    detected_topics: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeacherVideo":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        if payload.get("detected_topics") is None:
            payload["detected_topics"] = []
        return cls(**payload)


@dataclass
class VideoRoadmap:
    id: str = field(default_factory=lambda: new_id("rmp"))
    video_id: str = ""
    persona_id: str = ""
    title: str = ""
    summary: str = ""
    difficulty: str = "beginner"  # beginner|intermediate|advanced
    topics: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoRoadmap":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        if payload.get("topics") is None:
            payload["topics"] = []
        return cls(**payload)


@dataclass
class RoadmapPart:
    id: str = field(default_factory=lambda: new_id("prt"))
    roadmap_id: str = ""
    part_id: str = "part_1"  # human-readable ordering id from LLM
    order: int = 0
    title: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    transcript_chunk: str = ""
    summary: str = ""
    concepts: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    suggested_visuals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoadmapPart":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        for list_field in ("concepts", "equations", "examples", "suggested_visuals"):
            if payload.get(list_field) is None:
                payload[list_field] = []
        return cls(**payload)


@dataclass
class StudentSession:
    id: str = field(default_factory=lambda: new_id("ses"))
    student_id: str = ""
    persona_id: str = ""
    selected_topic: Optional[str] = None
    mode: Optional[str] = None  # "video_context" | "persona_only"
    current_roadmap_id: Optional[str] = None
    current_video_id: Optional[str] = None
    current_part_id: Optional[str] = None
    current_part_index: int = 0
    state: str = "greeting"  # see SESSION_STATES below
    matched_part_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    last_played_part_id: Optional[str] = None
    last_video_context_summary: str = ""
    last_part_was_final: bool = False
    next_suggested_topic: Optional[str] = None
    memory: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StudentSession":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        if payload.get("matched_part_ids") is None:
            payload["matched_part_ids"] = []
        if payload.get("memory") is None:
            payload["memory"] = {}
        return cls(**payload)


SESSION_STATES = (
    "greeting",
    "awaiting_topic",
    "topic_matching",
    "playing_video_part",
    "awaiting_part_feedback",
    "clarifying_part_doubt",
    "awaiting_clarification_feedback",
    "moving_to_next_part",
    "persona_only_confirmation",
    "persona_only_teaching",
    "completed",
)


@dataclass
class StudentMessage:
    id: str = field(default_factory=lambda: new_id("msg"))
    session_id: str = ""
    role: str = "student"  # "student" | "assistant" | "system"
    content: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StudentMessage":
        payload = {k: data.get(k) for k in cls.__dataclass_fields__}
        if payload.get("extra") is None:
            payload["extra"] = {}
        return cls(**payload)


@dataclass
class MissingTopicRequest:
    id: str = field(default_factory=lambda: new_id("mtr"))
    student_id: str = ""
    persona_id: str = ""
    topic: str = ""
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissingTopicRequest":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})
