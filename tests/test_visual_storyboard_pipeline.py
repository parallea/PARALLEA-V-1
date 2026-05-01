from __future__ import annotations

import json
import unittest

from backend.visuals.storyboard_validator import validate_storyboard
from backend.visuals.visual_planner import fallback_storyboard
from manim_renderer import heuristic_manim_payload
from teaching_pipeline import build_storyboard_frame_plan, heuristic_render_mode_selection, heuristic_segment_plan


def build_lesson_plan(
    *,
    topic: str,
    teaching_objective: str,
    answer_summary: str,
    key_ideas: list[str],
    teaching_steps: list[dict],
    key_formulas: list[dict] | None = None,
) -> dict:
    return {
        "topic": topic,
        "teaching_objective": teaching_objective,
        "answer_summary": answer_summary,
        "teaching_style": "Concept-first, visual reasoning, minimal text.",
        "key_ideas": key_ideas,
        "visualization_notes": key_ideas[:2],
        "key_formulas": key_formulas or [],
        "examples": [],
        "teaching_steps": teaching_steps,
        "follow_up": "What part should the learner test next?",
        "suggestions": ["Give a visual intuition", "Add a worked example"],
    }


def legacy_quality_proxy(question: str, lesson_plan: dict, segment_plan: dict) -> dict[str, float]:
    payloads = [heuristic_manim_payload(question, lesson_plan, segment) for segment in segment_plan.get("segments", [])]
    if not payloads:
        return {"overall_score": 0.0, "diversity": 0.0, "equation_restraint": 0.0, "motion_ratio": 0.0}
    diversity = len({payload.get("scene_type") for payload in payloads}) / len(payloads)
    equation_indices = [index for index, payload in enumerate(payloads) if payload.get("scene_type") == "equation_steps"]
    equation_restraint = 1.0 if not equation_indices or equation_indices[0] > 0 else 0.2
    motion_ratio = sum(
        1
        for payload in payloads
        if payload.get("scene_type") in {"axes_curve", "vector_axes", "cycle_loop", "process_flow", "comparison_cards"}
    ) / len(payloads)
    overall = round((diversity + equation_restraint + motion_ratio) / 3, 3)
    return {
        "overall_score": overall,
        "diversity": round(diversity, 3),
        "equation_restraint": round(equation_restraint, 3),
        "motion_ratio": round(motion_ratio, 3),
    }


class StoryboardPipelineTests(unittest.TestCase):
    maxDiff = None

    def test_forced_excalidraw_selection_overrides_mode_choice(self) -> None:
        question = "How does BFS work?"
        lesson_plan = build_lesson_plan(
            topic="Breadth-first search",
            teaching_objective="Understand BFS as level-by-level graph exploration controlled by a queue.",
            answer_summary="Breadth-first search expands a frontier one level at a time and uses a queue to preserve that order.",
            key_ideas=[
                "BFS explores level by level.",
                "The queue preserves the frontier order.",
            ],
            teaching_steps=[
                {
                    "step_id": "step_1",
                    "label": "See the frontier",
                    "key_idea": "The learner should see the wave of exploration.",
                    "explanation": "Show the frontier growing across the graph.",
                    "visual_focus": "Reveal graph nodes and highlight the frontier.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
            ],
        )
        segment = heuristic_segment_plan(lesson_plan)["segments"][0]

        selection = heuristic_render_mode_selection(
            question,
            lesson_plan,
            segment,
            1,
            preferred_visualization="excalidraw",
        )

        self.assertEqual(selection["render_mode"], "excalidraw")
        self.assertEqual(selection["fallback_mode"], "manim")
        self.assertIn("learner selected", selection["reason"].lower())

    def test_forced_excalidraw_uses_semantic_whiteboard_even_with_storyboard(self) -> None:
        question = "What is slope?"
        lesson_plan = build_lesson_plan(
            topic="Slope",
            teaching_objective="Understand slope as steepness and rise over run.",
            answer_summary="Slope tells you how steep a line is by comparing rise to run.",
            key_ideas=[
                "Slope is steepness.",
                "Rise over run measures that steepness.",
            ],
            teaching_steps=[
                {
                    "step_id": "step_1",
                    "label": "See the line",
                    "key_idea": "Start with the visual intuition.",
                    "explanation": "Show a line and label the rise and run.",
                    "visual_focus": "Line with rise-run labels.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
            ],
        )
        segment_plan = heuristic_segment_plan(lesson_plan)
        frame_plan = build_storyboard_frame_plan(
            question=question,
            lesson_plan=lesson_plan,
            segment_plan=segment_plan,
            segment=segment_plan["segments"][0],
            frame_number=1,
            storyboard={"scene_sequence": [{"scene_id": "scene_1"}]},
            preferred_visualization="excalidraw",
        )

        self.assertEqual(frame_plan["render_mode"], "excalidraw")
        self.assertEqual(frame_plan["visual_pipeline_path"], "semantic_whiteboard")
        self.assertIn("selected", frame_plan["reason"].lower())

    def test_math_slope_storyboard_prefers_graph_then_formula(self) -> None:
        question = "What is slope?"
        lesson_plan = build_lesson_plan(
            topic="Slope",
            teaching_objective="Understand slope as steepness and as rise over run.",
            answer_summary="Slope tells you how steep a line is. You can see it by how much the line rises when you move a certain amount horizontally. The formula comes after that picture.",
            key_ideas=[
                "Slope is steepness.",
                "Rise and run create a ratio.",
                "The formula summarizes the picture.",
            ],
            key_formulas=[{"formula": "m = Δy / Δx", "meaning": "slope is change in y over change in x", "when_to_use": "when you want the numerical ratio"}],
            teaching_steps=[
                {
                    "step_id": "step_1",
                    "label": "See the line",
                    "key_idea": "Slope feels like steepness before it feels like algebra.",
                    "explanation": "Start with a line and a moving point so the learner sees the steepness directly.",
                    "visual_focus": "Show a line, a moving point, and a rise-run step.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_2",
                    "label": "Compare triangles",
                    "key_idea": "Different step sizes on the same line keep the same ratio.",
                    "explanation": "Compare a small rise-run triangle and a larger one on the same line.",
                    "visual_focus": "Contrast two rise-run triangles on one line.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_3",
                    "label": "Name the ratio",
                    "key_idea": "The formula is a compact name for the visual ratio.",
                    "explanation": "Only after the graph intuition lands should the ratio become m equals delta y over delta x.",
                    "visual_focus": "Transform the rise-run picture into the slope formula.",
                    "example": "",
                    "formula": "m = Δy / Δx",
                    "formula_terms": [{"term": "Δy", "meaning": "vertical change"}, {"term": "Δx", "meaning": "horizontal change"}],
                    "visual_mode_hint": "manim",
                },
            ],
        )
        segment_plan = heuristic_segment_plan(lesson_plan)
        storyboard = fallback_storyboard(
            user_question=question,
            teaching_answer=lesson_plan["answer_summary"],
            subject="math",
            requested_depth="normal",
            lesson_plan=lesson_plan,
            segment_plan=segment_plan,
        )
        validation = validate_storyboard(storyboard, teaching_answer=lesson_plan["answer_summary"], subject="math", requested_depth="normal")
        sample_json = storyboard.to_dict()
        legacy = legacy_quality_proxy(question, lesson_plan, segment_plan)

        self.assertGreater(validation.score.overall_score, legacy["overall_score"])
        self.assertIn(sample_json["scene_sequence"][0]["scene_type"], {"graph_intuition", "rise_run_compare"})
        self.assertEqual(sample_json["scene_sequence"][0]["equations_usage"], "none")
        self.assertIn("progressive", sample_json["scene_sequence"][-1]["equations_usage"])
        self.assertGreaterEqual(validation.score.visual_diversity_score, 0.66)
        self.assertGreaterEqual(validation.score.motion_intent_score, 0.66)

    def test_physics_projectile_storyboard_uses_arc_and_vector_decomposition(self) -> None:
        question = "What is projectile motion?"
        lesson_plan = build_lesson_plan(
            topic="Projectile motion",
            teaching_objective="Understand projectile motion as horizontal motion plus vertical motion under gravity.",
            answer_summary="Projectile motion is a curved path because horizontal motion keeps going while gravity constantly changes the vertical motion. The key is to see one motion split into two independent components before you formalize it.",
            key_ideas=[
                "The path is curved.",
                "Horizontal and vertical motions can be treated separately.",
                "Gravity only changes the vertical component.",
            ],
            teaching_steps=[
                {
                    "step_id": "step_1",
                    "label": "Watch the arc",
                    "key_idea": "The learner should first see the whole path.",
                    "explanation": "Show the launched object tracing an arc from launch to landing.",
                    "visual_focus": "Animate a projectile path with launch, peak, and landing.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_2",
                    "label": "Split the motion",
                    "key_idea": "The single motion decomposes into horizontal and vertical pieces.",
                    "explanation": "Freeze the object mid-flight and show its velocity split into components.",
                    "visual_focus": "Decompose the velocity vector into horizontal and vertical arrows.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_3",
                    "label": "Connect back to the graph",
                    "key_idea": "The graph is a compact way to summarize the motion.",
                    "explanation": "Only after the motion intuition should the path become a height-versus-time style summary.",
                    "visual_focus": "Connect the curved path to a graph relationship without turning it into a wall of symbols.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
            ],
        )
        segment_plan = heuristic_segment_plan(lesson_plan)
        storyboard = fallback_storyboard(
            user_question=question,
            teaching_answer=lesson_plan["answer_summary"],
            subject="physics",
            requested_depth="normal",
            lesson_plan=lesson_plan,
            segment_plan=segment_plan,
        )
        validation = validate_storyboard(storyboard, teaching_answer=lesson_plan["answer_summary"], subject="physics", requested_depth="normal")
        sample_json = storyboard.to_dict()
        legacy = legacy_quality_proxy(question, lesson_plan, segment_plan)

        scene_types = [scene["scene_type"] for scene in sample_json["scene_sequence"]]
        self.assertGreater(validation.score.overall_score, legacy["overall_score"])
        self.assertIn("motion_arc", scene_types)
        self.assertIn("vector_decomposition", scene_types)
        self.assertTrue(all(scene["text_usage"] == "minimal labels only" for scene in sample_json["scene_sequence"]))
        self.assertGreaterEqual(validation.score.motion_intent_score, 0.75)
        self.assertGreaterEqual(validation.score.layout_variation_score, 0.66)

    def test_cs_bfs_storyboard_uses_graph_and_queue_frontier(self) -> None:
        question = "How does BFS work?"
        lesson_plan = build_lesson_plan(
            topic="Breadth-first search",
            teaching_objective="Understand BFS as level-by-level graph exploration controlled by a queue.",
            answer_summary="Breadth-first search starts at one node, visits every neighbor at the current depth, and uses a queue to remember which node to explore next. The visual idea is a frontier that expands one layer at a time.",
            key_ideas=[
                "BFS explores level by level.",
                "The frontier expands outward.",
                "A queue remembers the next nodes.",
            ],
            teaching_steps=[
                {
                    "step_id": "step_1",
                    "label": "See the frontier",
                    "key_idea": "The learner should see the wave of exploration.",
                    "explanation": "Start at one node and highlight the frontier expanding across the graph.",
                    "visual_focus": "Reveal the graph and spread the highlight one layer at a time.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_2",
                    "label": "Track the queue",
                    "key_idea": "The queue explains why the order stays level by level.",
                    "explanation": "Show the queue state changing as nodes are added and removed.",
                    "visual_focus": "Display queue boxes beside the graph while nodes are visited.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
                {
                    "step_id": "step_3",
                    "label": "Summarize the order",
                    "key_idea": "The final visited order comes from the frontier rule, not from luck.",
                    "explanation": "Compare the queue-driven level order to a depth-first style intuition so the learner sees the contrast.",
                    "visual_focus": "Contrast breadth-first level order with a different exploration habit.",
                    "example": "",
                    "formula": "",
                    "formula_terms": [],
                    "visual_mode_hint": "manim",
                },
            ],
        )
        segment_plan = heuristic_segment_plan(lesson_plan)
        storyboard = fallback_storyboard(
            user_question=question,
            teaching_answer=lesson_plan["answer_summary"],
            subject="cs",
            requested_depth="normal",
            lesson_plan=lesson_plan,
            segment_plan=segment_plan,
        )
        validation = validate_storyboard(storyboard, teaching_answer=lesson_plan["answer_summary"], subject="cs", requested_depth="normal")
        sample_json = storyboard.to_dict()
        legacy = legacy_quality_proxy(question, lesson_plan, segment_plan)

        self.assertGreater(validation.score.overall_score, legacy["overall_score"])
        self.assertIn(sample_json["scene_sequence"][0]["scene_type"], {"graph_traversal", "queue_frontier"})
        self.assertTrue(any("queue" in json.dumps(scene).lower() for scene in sample_json["scene_sequence"]))
        self.assertGreaterEqual(validation.score.visual_diversity_score, 0.66)
        self.assertGreaterEqual(validation.score.motion_intent_score, 0.66)


if __name__ == "__main__":
    unittest.main()
