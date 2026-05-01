"""JSON-file backed persistence layer for the persona-centered platform.

All entities live in `data/*.json` files, one list per file. Each entity is a
plain dict; dataclass helpers in `models.py` describe the canonical shape.

Designed to be swapped for SQLite/Postgres later without touching call sites.
"""
from .models import (
    MissingTopicRequest,
    PersonaPromptVersion,
    RoadmapPart,
    StudentMessage,
    StudentSession,
    TeacherPersona,
    TeacherVideo,
    User,
    VideoRoadmap,
)
from .repository import (
    messages_repo,
    missing_topics_repo,
    persona_prompts_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    sessions_repo,
    users_repo,
    videos_repo,
)

__all__ = [
    "User",
    "TeacherPersona",
    "PersonaPromptVersion",
    "TeacherVideo",
    "VideoRoadmap",
    "RoadmapPart",
    "StudentSession",
    "StudentMessage",
    "MissingTopicRequest",
    "users_repo",
    "personas_repo",
    "persona_prompts_repo",
    "videos_repo",
    "roadmaps_repo",
    "roadmap_parts_repo",
    "sessions_repo",
    "messages_repo",
    "missing_topics_repo",
]
