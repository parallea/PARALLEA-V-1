from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

os.environ.setdefault("MANIM_ALLOW_MATHTEX", "0")

from backend.services import answer_service  # noqa: E402
from manim_renderer import direct_manim_validation_error, render_manim_payload  # noqa: E402


FAKE_MANIM_CODE = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Interactive clarification", font_size=34, color=WHITE)
        title.to_edge(UP, buff=0.45)
        cause = Circle(radius=0.42, color=BLUE).shift(LEFT * 3)
        change = Rectangle(width=1.35, height=0.78, color=GREEN)
        result = Circle(radius=0.42, color=ORANGE).shift(RIGHT * 3)
        arrow_a = Arrow(cause.get_right(), change.get_left(), buff=0.18, color=YELLOW)
        arrow_b = Arrow(change.get_right(), result.get_left(), buff=0.18, color=YELLOW)
        dot = Dot(cause.get_center(), radius=0.08, color=YELLOW)
        caption = Text("Cause moves through a change", font_size=24, color=WHITE)
        caption.to_edge(DOWN, buff=0.7)
        self.play(Write(title), FadeIn(caption), run_time=0.7)
        self.play(Create(cause), FadeIn(dot), run_time=0.7)
        self.play(Create(arrow_a), dot.animate.move_to(change.get_center()), run_time=0.9)
        self.play(Create(change), Indicate(change, color=YELLOW), run_time=0.7)
        self.play(Create(arrow_b), dot.animate.move_to(result.get_center()), run_time=0.9)
        self.play(Create(result), Circumscribe(VGroup(cause, change, result), color=YELLOW), run_time=0.8)
        self.wait(0.8)
"""


async def fake_llm_json(task: str, system_prompt: str, user_prompt: str, **_: Any) -> dict[str, Any]:
    if task != "teaching_pipeline":
        raise AssertionError(f"Unexpected LLM task: {task}")
    if "boring text board" not in system_prompt:
        raise AssertionError("Combined teaching prompt did not include interactive visual rules.")
    mode = "persona_only_teaching" if "persona_only_teaching" in user_prompt else "video_context_clarification"
    prefix = "This is not from an uploaded video yet, but I can teach it in this style." if mode == "persona_only_teaching" else "Let's clarify the current video part."
    return {
        "speech": {
            "text": f"{prefix} First watch the cause, then the change, then the result. Does that make sense now?",
            "segments": [
                {"id": "seg_1", "start": 0.0, "end": 3.0, "text": prefix},
                {"id": "seg_2", "start": 3.0, "end": 6.0, "text": "First watch the cause move into the changing step."},
                {"id": "seg_3", "start": 6.0, "end": 9.0, "text": "Then connect that change to the final result."},
            ],
        },
        "visual": {
            "visualNeeded": True,
            "visualType": "manim",
            "style": "interactive_teacher_visual",
            "segments": [
                {"id": "vis_1", "start": 0.0, "end": 3.0, "matchesSpeechSegmentId": "seg_1", "description": "Reveal the setup."},
                {"id": "vis_2", "start": 3.0, "end": 6.0, "matchesSpeechSegmentId": "seg_2", "description": "Move the dot from cause to change."},
                {"id": "vis_3", "start": 6.0, "end": 9.0, "matchesSpeechSegmentId": "seg_3", "description": "Move the dot from change to result."},
            ],
            "manimCode": FAKE_MANIM_CODE,
        },
        "teachingControl": {
            "askFollowUp": "Does that make sense now?",
            "nextAction": "await_student_response",
        },
    }


async def empty_llm_json(task: str, system_prompt: str, user_prompt: str, **_: Any) -> dict[str, Any]:
    if task != "teaching_pipeline":
        raise AssertionError(f"Unexpected LLM task: {task}")
    return {}


async def main() -> None:
    answer_service.llm_json = fake_llm_json
    part = {
        "id": "part_test_1",
        "title": "Newton's second law",
        "summary": "Force changes motion by changing acceleration.",
        "transcript_chunk": "In this part the teacher connects force, mass, and acceleration.",
        "concepts": ["force", "mass", "acceleration"],
        "equations": ["F = ma"],
        "suggested_visuals": ["Move an object with a force arrow."],
    }
    video_payload = await answer_service.generate_teaching_response_with_visuals(
        mode="video_context_clarification",
        persona_prompt="Teach calmly with quick checks for understanding.",
        teacher_name="Demo Teacher",
        teacher_profession="Physics teacher",
        student_name="Student",
        topic="Newton's laws",
        student_query="I did not understand why acceleration changes.",
        current_roadmap_part=part,
        part_context=answer_service.build_roadmap_part_context({"title": "Motion"}, part),
        available_visual_mode="manim",
    )
    persona_payload = await answer_service.generate_teaching_response_with_visuals(
        mode="persona_only_teaching",
        persona_prompt="Teach calmly with quick checks for understanding.",
        teacher_name="Demo Teacher",
        teacher_profession="Physics teacher",
        student_name="Student",
        topic="Impulse",
        student_query="Please teach impulse.",
        available_visual_mode="manim",
    )

    for name, payload in [("video_context_clarification", video_payload), ("persona_only_teaching", persona_payload)]:
        visual = payload.get("visual") or {}
        speech = payload.get("speech") or {}
        assert speech.get("segments"), f"{name}: missing speech segments"
        assert visual.get("visualType") == "manim", f"{name}: visual is not Manim"
        assert visual.get("manimCode"), f"{name}: missing Manim code"
        assert not direct_manim_validation_error(visual["manimCode"]), f"{name}: Manim code failed validation"

    renderer_payload = {
        "renderer_version": "openai_direct_manim_v1",
        "scene_type": "openai_direct",
        "scene_class_name": "GeneratedScene",
        "manim_code": persona_payload["visual"]["manimCode"],
        "title": "Pipeline routing test",
        "subtitle": "Speech + Manim, not board",
        "duration_sec": 12,
        "segment_id": "test_teaching_pipeline_routing",
        "student_query": "Please teach impulse.",
    }
    start = time.perf_counter()
    first_render = render_manim_payload(renderer_payload, segment_id="test_teaching_pipeline_routing", frame_number=1)
    first_elapsed = round(time.perf_counter() - start, 3)
    second_render = render_manim_payload(renderer_payload, segment_id="test_teaching_pipeline_routing", frame_number=1)
    assert second_render.get("cache_hit") is True, "second render did not reuse cached Manim output"

    answer_service.llm_json = empty_llm_json
    fallback_payload = await answer_service.generate_teaching_response_with_visuals(
        mode="persona_only_teaching",
        persona_prompt="Teach calmly with quick checks for understanding.",
        teacher_name="Demo Teacher",
        teacher_profession="Physics teacher",
        student_name="Student",
        topic="Velocity",
        student_query="Teach velocity.",
        available_visual_mode="manim",
    )
    fallback_render = render_manim_payload(
        {
            "renderer_version": "openai_direct_manim_v1",
            "scene_type": "openai_direct",
            "scene_class_name": "GeneratedScene",
            "manim_code": fallback_payload["visual"]["manimCode"],
            "title": "Combined fallback test",
            "subtitle": "Local combined fallback",
            "duration_sec": 12,
            "segment_id": "test_teaching_pipeline_local_fallback",
            "student_query": "Teach velocity.",
        },
        segment_id="test_teaching_pipeline_local_fallback",
        frame_number=1,
    )

    print(
        json.dumps(
            {
                "video_context_clarification": {
                    "speech_segments": len(video_payload["speech"]["segments"]),
                    "visual_type": video_payload["visual"]["visualType"],
                    "board_primary": False,
                },
                "persona_only_teaching": {
                    "speech_segments": len(persona_payload["speech"]["segments"]),
                    "visual_type": persona_payload["visual"]["visualType"],
                    "board_primary": False,
                },
                "render": {
                    "first_elapsed_sec": first_elapsed,
                    "renderer_elapsed_sec": first_render.get("render_time_sec"),
                    "cache_hit_second_render": second_render.get("cache_hit"),
                    "video_url": first_render.get("video_url"),
                    "used_fallback": first_render.get("used_fallback"),
                },
                "local_combined_fallback": {
                    "speech_segments": len(fallback_payload["speech"]["segments"]),
                    "video_url": fallback_render.get("video_url"),
                    "used_renderer_fallback": fallback_render.get("used_fallback"),
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
