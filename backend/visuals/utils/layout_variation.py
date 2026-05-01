from __future__ import annotations

from typing import Any


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


LAYOUT_VARIANTS = {
    "left_visual_right_labels": "large visual on the left with tight labels or cue cards on the right",
    "center_morph": "single central composition that morphs through the scene",
    "progressive_reveal": "one visual structure built piece by piece in place",
    "compare_before_after": "two visual states contrasted before they are merged or connected",
    "top_bottom_causal_flow": "cause above, effect below, with visible flow between them",
    "radial_build": "a center concept with ideas or components radiating outward",
    "zoom_into_detail": "start wide, then focus into one active region",
    "timeline_style": "ordered progression across time or algorithmic steps",
}


def avoid_recent_layout_repetition(candidates: list[str], recent_layouts: list[str], *, max_repeat: int = 1) -> list[str]:
    if not recent_layouts:
        return candidates
    filtered = []
    for candidate in candidates:
        if recent_layouts.count(candidate) <= max_repeat - 1:
            filtered.append(candidate)
    return filtered or candidates


def choose_layout_variant(
    scene_type: str,
    subject: str,
    scene_index: int,
    *,
    recent_layouts: list[str] | None = None,
    preferred: str | None = None,
) -> str:
    recent = [clean_spaces(item).lower() for item in (recent_layouts or []) if clean_spaces(item)]
    preferred_clean = clean_spaces(preferred).lower()
    if preferred_clean in LAYOUT_VARIANTS:
        return preferred_clean

    candidates = {
        "graph_intuition": ["center_morph", "left_visual_right_labels", "zoom_into_detail"],
        "motion_arc": ["center_morph", "top_bottom_causal_flow", "left_visual_right_labels"],
        "vector_decomposition": ["center_morph", "compare_before_after", "left_visual_right_labels"],
        "graph_traversal": ["center_morph", "timeline_style", "left_visual_right_labels"],
        "queue_frontier": ["left_visual_right_labels", "timeline_style", "center_morph"],
        "cycle_flow": ["radial_build", "center_morph", "top_bottom_causal_flow"],
        "symbolic_formalize": ["left_visual_right_labels", "center_morph", "zoom_into_detail"],
    }.get(clean_spaces(scene_type).lower())
    if not candidates:
        if clean_spaces(subject).lower() in {"biology"}:
            candidates = ["top_bottom_causal_flow", "radial_build", "zoom_into_detail"]
        elif clean_spaces(subject).lower() in {"cs"}:
            candidates = ["timeline_style", "left_visual_right_labels", "center_morph"]
        else:
            candidates = ["center_morph", "compare_before_after", "progressive_reveal"]

    candidates = avoid_recent_layout_repetition(candidates, recent, max_repeat=1)
    return candidates[scene_index % len(candidates)]


def vary_scene_composition(scene_type: str, subject: str, scene_index: int, total_scenes: int, recent_layouts: list[str]) -> str:
    if scene_index == 0:
        preferred = "center_morph"
    elif scene_index == total_scenes - 1:
        preferred = "zoom_into_detail" if clean_spaces(subject).lower() in {"math", "physics"} else "compare_before_after"
    else:
        preferred = ""
    return choose_layout_variant(scene_type, subject, scene_index, recent_layouts=recent_layouts, preferred=preferred)

