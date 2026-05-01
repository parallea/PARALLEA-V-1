"""Small helpers for handling uploaded video files (thumbnail extraction,
filename normalization). Kept separate so the teacher router and the legacy
upload route can share the same behaviour without circular imports.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("parallea.video_assets")


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
    except Exception as exc:  # noqa: BLE001
        logger.warning("thumbnail extraction failed: %s", exc)
        return False


def safe_video_filename(video_id: str, original: str | None) -> str:
    suffix = ".mp4"
    if original:
        candidate = Path(original).suffix.lower()
        if candidate in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}:
            suffix = candidate
    return f"video_{video_id}{suffix}"
