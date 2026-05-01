"""Generic JSON-file repository.

Each repository wraps one JSON file (a list of dicts). Reads cache the parsed
list in memory; writes serialize back atomically (.tmp + os.replace).

Single-process FastAPI dev server is the target; a threading.RLock guards
concurrent request handlers. Swap for SQLite when scale demands it — call
sites only see `find`/`create`/`update`/`delete`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from config import DATA_DIR

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
    utcnow,
)


class JsonRepo:
    def __init__(self, path: Path, model_cls):
        self.path = path
        self.model_cls = model_cls
        self._lock = threading.RLock()
        self._cache: Optional[list[dict[str, Any]]] = None

    def _ensure_loaded(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            self._flush()
            return self._cache
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            raw = []
        if not isinstance(raw, list):
            raw = []
        self._cache = raw
        return self._cache

    def _flush(self) -> None:
        assert self._cache is not None
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._ensure_loaded())

    def get(self, entity_id: str) -> Optional[dict[str, Any]]:
        if not entity_id:
            return None
        with self._lock:
            for row in self._ensure_loaded():
                if row.get("id") == entity_id:
                    return dict(row)
        return None

    def find_one(self, predicate: Callable[[dict[str, Any]], bool]) -> Optional[dict[str, Any]]:
        with self._lock:
            for row in self._ensure_loaded():
                if predicate(row):
                    return dict(row)
        return None

    def find_all(self, predicate: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._ensure_loaded() if predicate(row)]

    def where(self, **kwargs) -> list[dict[str, Any]]:
        def match(row: dict[str, Any]) -> bool:
            return all(row.get(k) == v for k, v in kwargs.items())
        return self.find_all(match)

    def first_where(self, **kwargs) -> Optional[dict[str, Any]]:
        def match(row: dict[str, Any]) -> bool:
            return all(row.get(k) == v for k, v in kwargs.items())
        return self.find_one(match)

    def create(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        if not isinstance(payload, dict):
            raise TypeError("create() expects dict or dataclass with to_dict()")
        with self._lock:
            data = self._ensure_loaded()
            data.append(payload)
            self._flush()
        return dict(payload)

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        entity_id = payload.get("id")
        if not entity_id:
            raise ValueError("upsert requires id")
        with self._lock:
            data = self._ensure_loaded()
            for idx, row in enumerate(data):
                if row.get("id") == entity_id:
                    data[idx] = payload
                    self._flush()
                    return dict(payload)
            data.append(payload)
            self._flush()
        return dict(payload)

    def update(self, entity_id: str, fields: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._lock:
            data = self._ensure_loaded()
            for idx, row in enumerate(data):
                if row.get("id") == entity_id:
                    row.update(fields)
                    if "updated_at" in self.model_cls.__dataclass_fields__ and "updated_at" not in fields:
                        row["updated_at"] = utcnow()
                    data[idx] = row
                    self._flush()
                    return dict(row)
        return None

    def delete(self, entity_id: str) -> bool:
        with self._lock:
            data = self._ensure_loaded()
            new_data = [row for row in data if row.get("id") != entity_id]
            if len(new_data) == len(data):
                return False
            self._cache = new_data
            self._flush()
        return True

    def replace_all(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            self._cache = [dict(r) for r in rows]
            self._flush()


users_repo = JsonRepo(DATA_DIR / "users.json", User)
personas_repo = JsonRepo(DATA_DIR / "personas.json", TeacherPersona)
persona_prompts_repo = JsonRepo(DATA_DIR / "persona_prompts.json", PersonaPromptVersion)
videos_repo = JsonRepo(DATA_DIR / "teacher_videos.json", TeacherVideo)
roadmaps_repo = JsonRepo(DATA_DIR / "roadmaps.json", VideoRoadmap)
roadmap_parts_repo = JsonRepo(DATA_DIR / "roadmap_parts.json", RoadmapPart)
sessions_repo = JsonRepo(DATA_DIR / "student_sessions.json", StudentSession)
messages_repo = JsonRepo(DATA_DIR / "student_messages.json", StudentMessage)
missing_topics_repo = JsonRepo(DATA_DIR / "missing_topics.json", MissingTopicRequest)
