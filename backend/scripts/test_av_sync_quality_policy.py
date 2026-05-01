"""Lightweight checks for the clarity-over-length Manim policy.

Usage:
    python -m backend.scripts.test_av_sync_quality_policy
"""
from __future__ import annotations

import json

from backend.services import answer_service
from backend.services import session_manager
from manim_renderer import direct_manim_validation_error, visible_step_labels_detected


def _av_sync(estimated: float, *, spoken_count: int = 6, visual_steps: list[dict] | None = None) -> dict:
    steps = visual_steps if visual_steps is not None else [
        {"description": "Start with the real-world intuition"},
        {"description": "Show the relationship with arrows"},
        {"description": "End with the takeaway"},
    ]
    return {
        "estimated_spoken_duration_seconds": estimated,
        "spoken_segment_count": spoken_count,
        "visual_step_count": len(steps),
        "visual_steps": steps,
    }


def _policy(estimated: float, actual: float, *, spoken_count: int = 6, visual_steps: list[dict] | None = None) -> dict:
    return session_manager._duration_acceptance_policy(
        {"media_duration_seconds": actual},
        _av_sync(estimated, spoken_count=spoken_count, visual_steps=visual_steps),
    )


def main() -> int:
    clear_shorter = _policy(80.0, 40.0)
    assert clear_shorter["duration_accepted"] is True
    assert clear_shorter["duration_warning_only"] is True
    assert clear_shorter["repair_required"] is False
    assert clear_shorter["reason"] == "clarity_over_length"

    extremely_short = _policy(80.0, 8.0)
    assert extremely_short["repair_required"] is True
    assert extremely_short["quality_repair_reason"] == "visual_extremely_short"

    missing_core = _policy(80.0, 40.0, visual_steps=[{"description": "Show"}])
    assert missing_core["repair_required"] is True
    assert missing_core["quality_repair_reason"] == "visual_misses_most_core_concepts"

    cropped_code = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        label = Text("Readable label", font_size=24)
        label.shift(UP * 4.5)
        self.play(Write(label))
        self.wait(1)
"""
    crop_error = direct_manim_validation_error(cropped_code)
    assert crop_error and "layout risk" in crop_error
    flags = session_manager._static_quality_flags(cropped_code, validation_error=crop_error, visual_style="creative_safe")
    assert flags["crop_risk_detected"] is True

    crashing_code = "from manim import *\n\nclass GeneratedScene(Scene):\n    def construct(self)\n        pass\n"
    assert "python syntax error" in (direct_manim_validation_error(crashing_code) or "")

    step_label_code = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        self.play(Write(Text("Step 1")))
"""
    assert visible_step_labels_detected(step_label_code) is True

    system_prompt = answer_service._combined_teaching_system_prompt()
    assert "Quality and understandability are more important than matching the full audio duration" in system_prompt
    assert "Target a concise visual explanation, usually 30-60 seconds" in system_prompt
    assert "Teacher-persona visual style" in system_prompt
    assert "Internal visual quality rubric" in system_prompt

    practical_prompt = answer_service._build_combined_teaching_user_prompt(
        mode="persona_only_teaching",
        persona_prompt="I teach with practical real-world examples and intuition before formulas.",
        teacher_name="Demo Teacher",
        teacher_profession="Applied math teacher",
        student_name="Student",
        topic="percentages",
        student_query="How do discounts work?",
        part_context="EXAMPLES: shop discount, monthly budget\nSUGGESTED_VISUALS: price tag shrinking",
    )
    assert "Teacher visual persona context" in practical_prompt
    assert "practical" in practical_prompt.lower()
    assert "shop discount" in practical_prompt

    exam_prompt = answer_service._build_combined_teaching_user_prompt(
        mode="video_context_clarification",
        persona_prompt="I am exam-focused and show clean formulas, common question patterns, and quick checks.",
        teacher_name="Exam Coach",
        teacher_profession="Physics teacher",
        student_name="Student",
        topic="kinematics",
        student_query="How should I solve this in the exam?",
        part_context="EXAMPLES: projectile question\nSUGGESTED_VISUALS: clean axis diagram",
    )
    assert "exam-focused" in exam_prompt.lower()
    assert "clean exam-relevant diagrams" in system_prompt

    print(
        json.dumps(
            {
                "audio_80_manim_40": clear_shorter,
                "audio_80_manim_8": extremely_short,
                "audio_80_manim_40_missing_core": missing_core,
                "crop_validation_error": crop_error,
                "persona_prompt_checks": {
                    "practical_examples": True,
                    "exam_focused": True,
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
