from __future__ import annotations

from typing import Any


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


DEPTH_ALIASES = {
    "brief": "brief",
    "simple": "brief",
    "clarify": "normal",
    "normal": "normal",
    "medium": "normal",
    "advance": "detailed",
    "confirm_advance": "detailed",
    "detailed": "detailed",
}


def normalize_requested_depth(value: str) -> str:
    return DEPTH_ALIASES.get(clean_spaces(value).lower(), "normal")


def target_scene_count(requested_depth: str, segment_count: int = 0) -> int:
    depth = normalize_requested_depth(requested_depth)
    base = {"brief": 3, "normal": 4, "detailed": 5}[depth]
    if segment_count <= 0:
        return base
    if depth == "brief":
        return max(2, min(base, segment_count))
    if depth == "detailed":
        return max(3, min(max(base, segment_count), 6))
    return max(3, min(max(base, segment_count - 1), 5))


def pacing_style_for_depth(requested_depth: str, scene_count: int) -> str:
    depth = normalize_requested_depth(requested_depth)
    if depth == "brief":
        return f"Compressed to {scene_count} scenes, but each scene still leads with a strong visual move before text."
    if depth == "detailed":
        return f"Use {scene_count} scenes with a slower build, one comparison beat, and a late formal payoff."
    return f"Use {scene_count} scenes with a fast intuition hook, a mid-sequence transformation, and a restrained formal close."


def estimate_scene_duration(requested_depth: str, scene_index: int, total_scenes: int, pedagogical_role: str) -> float:
    depth = normalize_requested_depth(requested_depth)
    base = {"brief": 5.0, "normal": 6.2, "detailed": 7.0}[depth]
    role = clean_spaces(pedagogical_role).lower()
    if role in {"intuition", "hook"}:
        base += 0.4
    elif role in {"formalize", "application", "transfer"}:
        base += 0.6
    if scene_index == 0:
        base += 0.2
    if scene_index == total_scenes - 1:
        base += 0.2
    base += (scene_index % 2) * 0.35
    return round(clamp(base, 4.0, 9.5 if depth != "brief" else 7.2), 1)

