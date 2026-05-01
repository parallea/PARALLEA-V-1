from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard


def build_generic_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    del storyboard, context
    if scene.scene_type == "process_reveal":
        return {
            "scene_family": "process_flow",
            "data": {
                "nodes": scene.key_visual_objects[:4] or ["idea", "change", "result"],
                "connectors": ["leads to", "reveals", "clarifies"],
            },
        }
    if scene.scene_type in {"comparison_transform", "concept_metaphor"}:
        return {
            "scene_family": "comparison_transform",
            "data": {
                "left_title": "Intuition",
                "left_items": scene.key_visual_objects[:3] or ["anchor", "change"],
                "right_title": "Formal view",
                "right_items": scene.emphasis_points[:3] or ["takeaway"],
                "bridge_label": "same idea seen two ways",
                "equation_sequence": [],
            },
        }
    return {
        "scene_family": "symbolic_transform",
        "data": {
            "anchor_title": "Visual summary",
            "anchor_items": scene.key_visual_objects[:3] or ["core idea", "relation"],
            "equation_sequence": [],
            "focus_labels": scene.emphasis_points[:2],
        },
    }
