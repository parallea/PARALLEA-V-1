from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.excalidraw_adapter import excalidraw_plan_to_renderer_payload
from backend.services.frame_router import route_frames
from backend.services.gemini_scene_director import fallback_scene_output
from backend.services.intent_router import route_explanation_intent
from backend.services.presentation_sync import build_visual_payload
from backend.services.question_pipeline import build_question_pipeline
from backend.services.session_state import default_teaching_session_state
from backend.services.validators import normalize_excalidraw_frame_plan, normalize_gemini_scene_output


def sample_scene_output() -> dict:
    return {
        "answerMode": "simple_explain",
        "spokenAnswerSegments": [
            {"id": "segment_1", "start": "00:00:00", "end": "00:00:05", "text": "Start with the visual idea.", "purpose": "intro"},
            {"id": "segment_2", "start": "00:00:05", "end": "00:00:10", "text": "Then connect it to the formula.", "purpose": "formula"},
        ],
        "formulae": ["m = delta y / delta x"],
        "functions": [],
        "frames": [
            {
                "id": "frame_1",
                "sceneDescription": "A labeled line with rise and run.",
                "timelineStart": "00:00:00",
                "timelineEnd": "00:00:05",
                "formulae": [],
                "functionsToShow": [],
                "functionsToDraw": [],
                "visualizer": "excalidraw",
                "visualGoal": "Show the line and label rise and run.",
                "visualNotes": ["Keep one anchor diagram in view."],
                "analogy": "",
                "elementsNeeded": ["asset:cartesian_plane", "semantic:triangle"],
            },
            {
                "id": "frame_2",
                "sceneDescription": "Plot the relationship on axes.",
                "timelineStart": "00:00:05",
                "timelineEnd": "00:00:10",
                "formulae": ["m = delta y / delta x"],
                "functionsToShow": [
                    {
                        "label": "Line",
                        "expression": "y = 2x + 1",
                        "shouldShowOnScreen": True,
                        "shouldDrawOnGraph": True,
                        "graphNotes": "Show the positive slope.",
                    }
                ],
                "functionsToDraw": [
                    {
                        "label": "Line",
                        "expression": "y = 2x + 1",
                        "shouldShowOnScreen": True,
                        "shouldDrawOnGraph": True,
                        "graphNotes": "Show the positive slope.",
                    }
                ],
                "visualizer": "manim",
                "visualGoal": "Graph the line while introducing the slope formula.",
                "visualNotes": ["Draw axes before the function."],
                "analogy": "",
                "elementsNeeded": [],
            },
        ],
    }


class IntentRouterTests(unittest.TestCase):
    def test_detects_repeat_visualize_and_brief_variants(self) -> None:
        repeat_intent = route_explanation_intent("Can you please repeat it?")
        brief_intent = route_explanation_intent("Briefly explain slope")
        visualize_intent = route_explanation_intent("Help me visualize projectile motion")

        self.assertEqual(repeat_intent["mode"], "repeat_previous")
        self.assertEqual(brief_intent["mode"], "brief_explain")
        self.assertEqual(visualize_intent["mode"], "visualize")
        self.assertTrue(visualize_intent["wantsVisuals"])
        self.assertTrue(visualize_intent["useRealLifeExample"])
        self.assertEqual(brief_intent["normalizedQuestion"].lower(), "slope")


class ValidationTests(unittest.TestCase):
    def test_scene_validation_filters_invalid_excalidraw_elements(self) -> None:
        fallback = sample_scene_output()
        raw = {
            "answerMode": "visualize",
            "spokenAnswerSegments": fallback["spokenAnswerSegments"],
            "formulae": fallback["formulae"],
            "functions": fallback["functions"],
            "frames": [
                {
                    "id": "frame_1",
                    "sceneDescription": "A board frame.",
                    "timelineStart": "00:00:00",
                    "timelineEnd": "00:00:05",
                    "formulae": [],
                    "functionsToShow": [],
                    "functionsToDraw": [],
                    "visualizer": "excalidraw",
                    "visualGoal": "Use only allowed elements.",
                    "visualNotes": ["Minimal frame."],
                    "elementsNeeded": ["asset:cartesian_plane", "asset:not_real"],
                }
            ],
        }

        normalized = normalize_gemini_scene_output(
            raw,
            fallback=fallback,
            forced_mode="visualize",
            allowed_excalidraw_elements={"asset:cartesian_plane", "semantic:triangle"},
        )

        self.assertEqual(normalized["frames"][0]["elementsNeeded"], ["asset:cartesian_plane"])

    def test_excalidraw_plan_rejects_unknown_assets(self) -> None:
        fallback = {
            "frameId": "frame_1",
            "title": "Fallback",
            "elementsToUse": [{"assetId": "asset:cartesian_plane", "label": "Plane", "positionHint": "center", "purpose": "Anchor"}],
            "textLabels": [],
            "arrows": [],
            "sequence": [{"step": 1, "action": "place_asset", "targetIds": ["asset:cartesian_plane"]}],
        }
        normalized = normalize_excalidraw_frame_plan(
            {
                "frameId": "frame_1",
                "elementsToUse": [{"assetId": "asset:fake", "positionHint": "center", "purpose": "Fake"}],
                "textLabels": [],
                "arrows": [],
                "sequence": [{"step": 1, "action": "place_asset", "targetIds": ["asset:fake"]}],
            },
            fallback=fallback,
            allowed_element_ids={"asset:cartesian_plane"},
        )
        self.assertEqual(normalized, fallback)


class RoutingAndSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_visualizer_routing_prefers_excalidraw_for_concepts_and_manim_for_graphs(self) -> None:
        with patch("backend.services.excalidraw_adapter.gemini_client", None), patch("backend.services.manim_adapter.gemini_client", None):
            frame_sequence, outputs = await route_frames(
                question="Explain slope",
                scene_output=sample_scene_output(),
                context="Slope compares rise to run.",
            )

        self.assertEqual(frame_sequence[0]["render_mode"], "excalidraw")
        self.assertEqual(frame_sequence[1]["render_mode"], "manim")
        self.assertEqual(outputs[0]["visualizer"], "excalidraw")
        self.assertEqual(outputs[1]["visualizer"], "manim")

    async def test_repeat_previous_reuses_explanation_and_scene_state(self) -> None:
        state = default_teaching_session_state()
        state["lastQuestion"] = "Explain slope"
        state["lastIntent"] = "simple_explain"
        state["lastExplanation"] = "Start with the visual idea. Then connect it to the formula."
        state["lastSceneOutput"] = sample_scene_output()
        state["lastSpokenSegments"] = sample_scene_output()["spokenAnswerSegments"]
        state["lastFormulae"] = sample_scene_output()["formulae"]
        state["lastFunctions"] = sample_scene_output()["functions"]
        state["lastFrames"] = sample_scene_output()["frames"]
        state["lastVisualizerOutputs"] = [{"frameId": "frame_1", "visualizer": "excalidraw", "plan": {}}]

        with patch("backend.services.explanation_generator.groq_client", None), patch("backend.services.explanation_generator.gemini_client", None), patch("backend.services.gemini_scene_director.gemini_client", None), patch("backend.services.excalidraw_adapter.gemini_client", None), patch("backend.services.manim_adapter.gemini_client", None):
            pipeline = await build_question_pipeline(
                question="Can you please repeat it",
                context="Slope compares rise to run.",
                title="Slope",
                learner_request="Can you please repeat it",
                session_state=state,
                preferred_visualization="excalidraw",
            )

        self.assertTrue(pipeline["pipeline_debug"]["previousStateReuse"]["explanationReused"])
        self.assertTrue(pipeline["pipeline_debug"]["previousStateReuse"]["sceneReused"])
        self.assertEqual(pipeline["answer"], "Start with the visual idea. Then connect it to the formula.")

    async def test_synced_visual_payload_uses_timed_frames(self) -> None:
        with patch("backend.services.excalidraw_adapter.gemini_client", None), patch("backend.services.manim_adapter.gemini_client", None):
            frame_sequence, _ = await route_frames(
                question="Explain slope",
                scene_output=sample_scene_output(),
                context="Slope compares rise to run.",
            )
        payload = build_visual_payload(frame_sequence)
        self.assertEqual(len(payload["segments"]), 2)
        self.assertEqual(payload["segments"][0]["start_pct"], 0.0)
        self.assertEqual(payload["segments"][-1]["end_pct"], 1.0)

    def test_excalidraw_renderer_payload_keeps_allowed_elements_only(self) -> None:
        plan = {
            "frameId": "frame_1",
            "title": "Slope board",
            "elementsToUse": [
                {"assetId": "asset:cartesian_plane", "label": "Plane", "positionHint": "center", "purpose": "Anchor"},
                {"assetId": "semantic:triangle", "label": "Triangle", "positionHint": "left", "purpose": "Rise and run"},
            ],
            "textLabels": [{"text": "rise over run", "positionHint": "bottom"}],
            "arrows": [],
            "sequence": [{"step": 1, "action": "place_asset", "targetIds": ["asset:cartesian_plane"]}],
        }
        payload = excalidraw_plan_to_renderer_payload(plan, sample_scene_output()["frames"][0])
        asset_ids = [item["name"] for item in payload["assets"]]
        object_kinds = [item["kind"] for item in payload["objects"]]
        self.assertIn("cartesian_plane", asset_ids)
        self.assertIn("triangle", object_kinds)

    def test_fallback_scene_output_defaults_to_excalidraw_for_conceptual_prompt(self) -> None:
        intent = route_explanation_intent("Explain how photosynthesis works")
        output = fallback_scene_output(
            intent=intent,
            question="How photosynthesis works",
            explanation_package={"explanation": "Plants use light energy to build sugar from carbon dioxide and water.", "formulae": [], "functions": []},
        )
        self.assertEqual(output["frames"][0]["visualizer"], "excalidraw")


if __name__ == "__main__":
    unittest.main()
