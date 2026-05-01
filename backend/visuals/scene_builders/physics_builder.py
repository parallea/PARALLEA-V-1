from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard, clean_spaces


def build_physics_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    concept = clean_spaces(storyboard.concept_summary).lower()
    if scene.scene_type == "motion_arc":
        return {
            "scene_family": "trajectory_decomposition",
            "data": {
                "x_label": "horizontal distance",
                "y_label": "height",
                "trajectory_points": [[0, 0], [1, 1.4], [2, 2.4], [3, 2.8], [4, 2.4], [5, 1.4], [6, 0]],
                "markers": [
                    {"point": [0, 0], "label": "launch"},
                    {"point": [3, 2.8], "label": "peak"},
                    {"point": [6, 0], "label": "landing"},
                ],
                "vectors": [
                    {"origin": [0, 0], "vector": [1.3, 1.6], "label": "launch velocity"},
                    {"origin": [3, 2.8], "vector": [1.0, 0], "label": "horizontal"},
                    {"origin": [3, 2.8], "vector": [0, -1.2], "label": "gravity"},
                ],
                "equation_sequence": [] if "projectile" in concept else [],
            },
        }
    if scene.scene_type == "vector_decomposition":
        return {
            "scene_family": "vector_decomposition",
            "data": {
                "main_vector": [2.8, 2.6],
                "components": [{"vector": [2.8, 0], "label": "horizontal"}, {"vector": [0, 2.6], "label": "vertical"}],
                "result_label": "motion splits into independent directions",
                "equation_sequence": [],
            },
        }
    return {
        "scene_family": "graph_motion",
        "data": {
            "x_label": "time",
            "y_label": "height",
            "points": [[0, 0], [1, 1.4], [2, 2.4], [3, 2.8], [4, 2.4], [5, 1.4], [6, 0]],
            "curve_kind": "parabola",
            "moving_point_label": "object",
            "annotations": [{"point": [3, 2.8], "label": "gravity bends the path"}],
            "guides": [],
            "equation_sequence": ["x changes steadily", "y is pulled down by gravity"],
        },
    }

