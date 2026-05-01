from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard, clean_spaces


def _formula(context: dict[str, Any], fallback: str = "") -> str:
    lesson_plan = context.get("lesson_plan") if isinstance(context.get("lesson_plan"), dict) else {}
    for item in (lesson_plan.get("key_formulas") or []):
        if isinstance(item, dict) and clean_spaces(item.get("formula")):
            return clean_spaces(item.get("formula"))
    return fallback


def build_math_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    concept = clean_spaces(storyboard.concept_summary).lower()
    if scene.scene_type == "graph_intuition":
        return {
            "scene_family": "graph_motion",
            "data": {
                "x_label": "run" if "slope" in concept else "input",
                "y_label": "rise" if "slope" in concept else "output",
                "points": [[-1, -1], [0, 0], [1, 1], [2, 2]],
                "curve_kind": "line",
                "moving_point_label": "point on the line",
                "annotations": [
                    {"point": [1, 1], "label": "same steepness anywhere"},
                    {"point": [2, 2], "label": "rise matches run here"},
                ],
                "guides": [
                    {"from": [0, 0], "to": [1, 0], "label": "run"},
                    {"from": [1, 0], "to": [1, 1], "label": "rise"},
                ],
                "equation_sequence": [],
            },
        }
    if scene.scene_type == "rise_run_compare":
        return {
            "scene_family": "comparison_transform",
            "data": {
                "left_title": "Small step",
                "left_items": ["run 1", "rise 1", "same steepness"],
                "right_title": "Bigger step",
                "right_items": ["run 2", "rise 2", "same ratio"],
                "bridge_label": "slope stays the same",
                "equation_sequence": [],
            },
        }
    if scene.scene_type == "geometry_construction":
        return {
            "scene_family": "geometry_build",
            "data": {
                "points": [[-3, -1.5], [3, -1.5], [1.5, 1.5]],
                "labels": ["A", "B", "C"],
                "highlights": [
                    {"kind": "side", "indices": [0, 1], "label": "run"},
                    {"kind": "side", "indices": [1, 2], "label": "rise"},
                ],
                "equation_sequence": [],
            },
        }
    return {
        "scene_family": "symbolic_transform",
        "data": {
            "anchor_title": "Steepness becomes a ratio",
            "anchor_items": ["rise", "run", "same line"],
            "equation_sequence": [_formula(context, "m = rise / run"), "m = Δy / Δx"],
            "focus_labels": ["Equation arrives after the picture", "Symbols summarize the visual relation"],
        },
    }

