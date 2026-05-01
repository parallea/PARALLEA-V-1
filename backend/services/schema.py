from __future__ import annotations

from typing import Any


EXPLANATION_MODES = {"simple_explain", "brief_explain", "repeat_previous", "visualize"}
VISUALIZER_VALUES = {"excalidraw", "manim"}
SPOKEN_PURPOSE_VALUES = {"intro", "core_explanation", "example", "formula", "summary"}
EXCALIDRAW_SEQUENCE_ACTIONS = {"place_asset", "show_text", "draw_arrow", "highlight"}
MANIM_OBJECT_TYPES = {"text", "mathtex", "axes", "plot", "shape", "arrow"}


def explanation_intent_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "rawQuestion": {"type": "string"},
            "normalizedQuestion": {"type": "string"},
            "mode": {"type": "string", "enum": sorted(EXPLANATION_MODES)},
            "wantsVisuals": {"type": "boolean"},
            "wantsRepeat": {"type": "boolean"},
            "wantsFormulae": {"type": "boolean"},
            "wantsFunctionGraph": {"type": "boolean"},
            "useRealLifeExample": {"type": "boolean"},
        },
        "required": [
            "rawQuestion",
            "normalizedQuestion",
            "mode",
            "wantsVisuals",
            "wantsRepeat",
            "wantsFormulae",
            "wantsFunctionGraph",
            "useRealLifeExample",
        ],
    }


def function_spec_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "expression": {"type": "string"},
            "shouldShowOnScreen": {"type": "boolean"},
            "shouldDrawOnGraph": {"type": "boolean"},
            "graphNotes": {"type": "string"},
        },
        "required": ["label", "expression", "shouldShowOnScreen", "shouldDrawOnGraph"],
    }


def spoken_segment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "text": {"type": "string"},
            "purpose": {"type": "string", "enum": sorted(SPOKEN_PURPOSE_VALUES)},
        },
        "required": ["id", "start", "end", "text", "purpose"],
    }


def visual_frame_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "sceneDescription": {"type": "string"},
            "timelineStart": {"type": "string"},
            "timelineEnd": {"type": "string"},
            "formulae": {"type": "array", "items": {"type": "string"}},
            "functionsToShow": {"type": "array", "items": function_spec_schema()},
            "functionsToDraw": {"type": "array", "items": function_spec_schema()},
            "visualizer": {"type": "string", "enum": sorted(VISUALIZER_VALUES)},
            "visualGoal": {"type": "string"},
            "visualNotes": {"type": "array", "items": {"type": "string"}},
            "analogy": {"type": "string"},
            "elementsNeeded": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "id",
            "sceneDescription",
            "timelineStart",
            "timelineEnd",
            "formulae",
            "functionsToShow",
            "functionsToDraw",
            "visualizer",
            "visualGoal",
            "visualNotes",
        ],
    }


def gemini_scene_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "answerMode": {"type": "string", "enum": sorted(EXPLANATION_MODES)},
            "spokenAnswerSegments": {"type": "array", "items": spoken_segment_schema()},
            "formulae": {"type": "array", "items": {"type": "string"}},
            "functions": {"type": "array", "items": function_spec_schema()},
            "frames": {"type": "array", "items": visual_frame_schema()},
        },
        "required": ["answerMode", "spokenAnswerSegments", "formulae", "functions", "frames"],
    }


def explanation_generator_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "explanation": {"type": "string"},
            "followUp": {"type": "string"},
            "formulae": {"type": "array", "items": {"type": "string"}},
            "functions": {"type": "array", "items": function_spec_schema()},
        },
        "required": ["title", "explanation", "followUp", "formulae", "functions"],
    }


def excalidraw_frame_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "frameId": {"type": "string"},
            "title": {"type": "string"},
            "elementsToUse": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "assetId": {"type": "string"},
                        "label": {"type": "string"},
                        "positionHint": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                    "required": ["assetId", "positionHint", "purpose"],
                },
            },
            "textLabels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "positionHint": {"type": "string"},
                    },
                    "required": ["text", "positionHint"],
                },
            },
            "arrows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["from", "to"],
                },
            },
            "sequence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "number"},
                        "action": {"type": "string", "enum": sorted(EXCALIDRAW_SEQUENCE_ACTIONS)},
                        "targetIds": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["step", "action", "targetIds"],
                },
            },
        },
        "required": ["frameId", "elementsToUse", "textLabels", "arrows", "sequence"],
    }


def manim_frame_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "frameId": {"type": "string"},
            "sceneSummary": {"type": "string"},
            "objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string", "enum": sorted(MANIM_OBJECT_TYPES)},
                        "content": {"type": "string"},
                        "expression": {"type": "string"},
                        "animation": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["type"],
                },
            },
            "sequence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "number"},
                        "action": {"type": "string"},
                        "targetIds": {"type": "array", "items": {"type": "string"}},
                        "narrationCue": {"type": "string"},
                    },
                    "required": ["step", "action", "targetIds"],
                },
            },
        },
        "required": ["frameId", "sceneSummary", "objects", "sequence"],
    }
