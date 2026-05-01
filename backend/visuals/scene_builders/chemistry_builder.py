from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard


def build_chemistry_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    del storyboard, context
    if scene.scene_type == "structure_compare":
        return {
            "scene_family": "comparison_transform",
            "data": {
                "left_title": "Before",
                "left_items": ["reactants grouped", "bonds ready to change"],
                "right_title": "After",
                "right_items": ["products regrouped", "new structure"],
                "bridge_label": "atoms stay, arrangement changes",
                "equation_sequence": [],
            },
        }
    if scene.scene_type == "particle_motion":
        return {
            "scene_family": "process_flow",
            "data": {
                "nodes": ["particles spread", "collide", "rearrange"],
                "connectors": ["motion", "interaction"],
            },
        }
    return {
        "scene_family": "symbolic_transform",
        "data": {
            "anchor_title": "Reaction summary",
            "anchor_items": ["particle view", "structure change", "conservation"],
            "equation_sequence": ["reactants -> products"],
            "focus_labels": ["Equation compresses the structural story"],
        },
    }

