from __future__ import annotations

from pathlib import Path

from config import (
    AUDIO_DIR,
    BASE_DIR,
    MANIM_DEBUG_DIR,
    MANIM_PUBLIC_OUTPUT_DIR,
    MANIM_RUNTIME_DIR,
    RENDERS_DIR,
)


GENERATED_RELOAD_EXCLUDE_NAMES = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "audio-response",
    "data/renders",
    "manim_runtime",
    "public/generated",
    "rendered-scenes",
    "uploads",
}

GENERATED_RELOAD_EXCLUDE_PATHS = (
    RENDERS_DIR,
    BASE_DIR / "rendered-scenes",
    BASE_DIR / "audio-response",
    BASE_DIR / "public" / "generated",
    BASE_DIR / "manim_runtime",
    MANIM_RUNTIME_DIR,
    MANIM_DEBUG_DIR,
    MANIM_PUBLIC_OUTPUT_DIR,
    AUDIO_DIR,
)

UVICORN_RELOAD_EXCLUDES = [
    ".git/*",
    ".idea/*",
    ".pytest_cache/*",
    ".venv/*",
    "__pycache__/*",
    "audio-response/*",
    "data/renders/*",
    "manim_runtime/*",
    "public/generated/*",
    "rendered-scenes/*",
    "uploads/*",
    "*.mp4",
    "*.mp3",
    "*.wav",
    "*.webm",
    "*.render.log",
    "*.metadata.json",
]


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path.absolute()


def is_generated_runtime_path(path: str | Path) -> bool:
    candidate = _safe_resolve(Path(path))
    for runtime_path in GENERATED_RELOAD_EXCLUDE_PATHS:
        root = _safe_resolve(Path(runtime_path))
        if candidate == root or root in candidate.parents:
            return True
    try:
        rel = candidate.relative_to(_safe_resolve(BASE_DIR))
        rel_posix = rel.as_posix()
    except Exception:
        rel_posix = candidate.as_posix()
    return any(rel_posix == name or rel_posix.startswith(f"{name}/") for name in GENERATED_RELOAD_EXCLUDE_NAMES)


def build_uvicorn_reload_kwargs() -> dict:
    return {
        "reload": True,
        "reload_dirs": [str(BASE_DIR)],
        "reload_excludes": UVICORN_RELOAD_EXCLUDES,
    }
