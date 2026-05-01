from __future__ import annotations

from typing import Any

from ..storyboard_schema import ScenePlan, Storyboard, clean_spaces


def build_cs_scene(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    concept = clean_spaces(storyboard.concept_summary).lower()
    if scene.scene_type in {"graph_traversal", "queue_frontier"}:
        return {
            "scene_family": "queue_frontier",
            "data": {
                "nodes": [
                    {"id": "A", "pos": [-3.0, 1.5]},
                    {"id": "B", "pos": [-1.0, 2.3]},
                    {"id": "C", "pos": [-1.0, 0.6]},
                    {"id": "D", "pos": [1.0, 2.3]},
                    {"id": "E", "pos": [1.0, 0.6]},
                    {"id": "F", "pos": [3.0, 1.5]},
                ],
                "edges": [["A", "B"], ["A", "C"], ["B", "D"], ["C", "E"], ["D", "F"], ["E", "F"]],
                "visit_order": ["A", "B", "C", "D", "E", "F"],
                "queue_states": [["A"], ["B", "C"], ["C", "D"], ["D", "E"], ["E", "F"], ["F"]],
                "result_label": "BFS expands one layer at a time" if "bfs" in concept else "queue drives the next visit",
            },
        }
    if scene.scene_type == "state_transition":
        return {
            "scene_family": "process_flow",
            "data": {
                "nodes": ["visit start", "enqueue neighbors", "dequeue next", "repeat by level"],
                "connectors": ["then", "then", "loop"],
            },
        }
    return {
        "scene_family": "comparison_transform",
        "data": {
            "left_title": "DFS style",
            "left_items": ["go deep first", "stack-like path"],
            "right_title": "BFS style",
            "right_items": ["level by level", "queue frontier"],
            "bridge_label": "same graph, different exploration order",
            "equation_sequence": [],
        },
    }

