from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard


def build_biology_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    del storyboard, context
    if scene.scene_type == "cycle_flow":
        return {
            "scene_family": "cycle_flow",
            "data": {
                "nodes": ["body", "right heart", "lungs", "left heart"],
                "center_label": "blood circulation",
                "relationship_label": "deoxygenated out, oxygenated back",
            },
        }
    if scene.scene_type == "comparison_transform":
        return {
            "scene_family": "comparison_transform",
            "data": {
                "left_title": "To the lungs",
                "left_items": ["low oxygen", "pick up oxygen"],
                "right_title": "To the body",
                "right_items": ["high oxygen", "deliver oxygen"],
                "bridge_label": "same loop, different job",
                "equation_sequence": [],
            },
        }
    return {
        "scene_family": "process_flow",
        "data": {
            "nodes": ["body", "vena cava", "right heart", "lungs", "left heart", "aorta"],
            "connectors": ["returns", "pumps", "oxygenates", "pumps", "delivers"],
        },
    }

