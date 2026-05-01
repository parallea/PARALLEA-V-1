from __future__ import annotations

from typing import Any


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


ANIMATION_PATTERNS: dict[str, list[str]] = {
    "reveal_then_transform": [
        "Reveal the main visual anchor before any formal label",
        "Transform the anchor into the next representation instead of replacing it with text",
        "Hold the transformed state long enough for the learner to register the idea",
    ],
    "trace_then_label": [
        "Trace the path or shape first so the motion carries meaning",
        "Label only the key relationship after the learner has seen the pattern",
        "Use a local highlight to lock attention onto the important change",
    ],
    "compare_and_merge": [
        "Place two contrasting visuals side by side",
        "Highlight what stays fixed and what changes",
        "Merge the contrast into one distilled takeaway",
    ],
    "decompose_and_rebuild": [
        "Show the whole object or process first",
        "Split it into meaningful parts",
        "Rebuild the parts into a clearer mental model",
    ],
    "focus_shift": [
        "Keep the full scene visible",
        "Shift attention from one active region to the next",
        "Use scale or highlight rather than adding more text",
    ],
    "local_highlight": [
        "Keep the base visual stable",
        "Pulse or outline the critical relationship",
        "Return to the full picture after the emphasis lands",
    ],
    "progressive_equation_build": [
        "Delay the equation until the intuition is already on screen",
        "Build the equation in small chunks",
        "Tie each symbolic piece back to the visual anchor",
    ],
    "graph_to_equation": [
        "Start with the graph or geometric relation and transform attention onto the measurable parts",
        "Morph the marked quantities into the symbols that matter",
        "Introduce the equation as a compact summary of the picture",
    ],
    "object_to_symbol": [
        "Begin with a moving object or diagram",
        "Transform the object's parts into variables or symbols",
        "Keep the object visible while the symbols appear",
    ],
    "symbol_to_graph": [
        "Start from the minimal symbolic statement",
        "Morph it into a graph or spatial picture",
        "Use the graph as the final source of intuition",
    ],
    "frontier_expand": [
        "Highlight the current frontier",
        "Expand only one level at a time",
        "Track the queue or active state as it changes",
    ],
}


def pattern_steps(name: str, key_objects: list[str], emphasis_points: list[str]) -> list[str]:
    steps = list(ANIMATION_PATTERNS.get(clean_spaces(name).lower(), []))
    if not steps:
        steps = list(ANIMATION_PATTERNS["reveal_then_transform"])
    if key_objects:
        steps[0] = f"{steps[0].rstrip('.')} around {', '.join(key_objects[:2])}."
    if emphasis_points:
        steps[-1] = f"{steps[-1].rstrip('.')} Focus on {emphasis_points[0]}."
    return steps


def recommended_patterns_for_scene(scene_type: str, subject: str) -> list[str]:
    scene_key = clean_spaces(scene_type).lower()
    subject_key = clean_spaces(subject).lower()
    if scene_key in {"motion_arc", "graph_intuition", "graph_connection"}:
        return ["trace_then_label", "graph_to_equation"]
    if scene_key in {"vector_decomposition", "particle_motion", "layered_structure"}:
        return ["decompose_and_rebuild", "focus_shift"]
    if scene_key in {"queue_frontier", "graph_traversal", "state_transition"}:
        return ["frontier_expand", "focus_shift"]
    if scene_key in {"symbolic_formalize"}:
        return ["progressive_equation_build", "object_to_symbol"]
    if scene_key in {"comparison_transform", "structure_compare", "rise_run_compare"}:
        return ["compare_and_merge", "local_highlight"]
    if subject_key == "math":
        return ["trace_then_label", "progressive_equation_build"]
    return ["reveal_then_transform", "focus_shift"]
