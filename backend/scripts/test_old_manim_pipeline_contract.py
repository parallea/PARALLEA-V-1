from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MANIM_ALLOW_MATHTEX", "0")

from backend.services import question_pipeline  # noqa: E402
from teaching_pipeline import materialize_frame_plan  # noqa: E402


PARALLEA_SCENE = """from manim import *

class ParalleaGeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Cause to result", font_size=34, color=WHITE).to_edge(UP, buff=0.45)
        left = Circle(radius=0.42, color=BLUE).shift(LEFT * 3)
        mid = Rectangle(width=1.35, height=0.72, color=GREEN)
        right = Circle(radius=0.42, color=ORANGE).shift(RIGHT * 3)
        dot = Dot(left.get_center(), radius=0.08, color=YELLOW)
        arrow_a = Arrow(left.get_right(), mid.get_left(), buff=0.15, color=YELLOW)
        arrow_b = Arrow(mid.get_right(), right.get_left(), buff=0.15, color=YELLOW)
        caption = Text("Watch the change move step by step", font_size=24, color=WHITE).to_edge(DOWN, buff=0.7)
        self.play(Write(title), FadeIn(caption), run_time=0.6)
        self.play(Create(left), FadeIn(dot), run_time=0.6)
        self.play(Create(arrow_a), dot.animate.move_to(mid.get_center()), run_time=0.8)
        self.play(Create(mid), Indicate(mid, color=YELLOW), run_time=0.6)
        self.play(Create(arrow_b), dot.animate.move_to(right.get_center()), run_time=0.8)
        self.play(Create(right), Circumscribe(VGroup(left, mid, right), color=YELLOW), run_time=0.7)
        self.wait(0.6)
"""


async def fake_openai_manim_pipeline(**_: Any) -> dict[str, Any]:
    segments = [
        ("segment_1", "00:00:00", "00:00:04", "First, identify the starting idea."),
        ("segment_2", "00:00:04", "00:00:08", "Then watch what changes in the middle."),
        ("segment_3", "00:00:08", "00:00:12", "Finally, connect that change to the result."),
    ]
    return {
        "title": "Old Manim pipeline contract",
        "answer": "First identify the start, then the change, then the result.",
        "follow_up": "Does that make sense now?",
        "suggestions": ["Explain more slowly", "Show another example"],
        "formulae": [],
        "speech": {
            "segments": [
                {"id": sid, "start": start, "end": end, "text": text, "purpose": "core_explanation", "visual_cue": text}
                for sid, start, end, text in segments
            ]
        },
        "manim": {
            "scene_class_name": "ParalleaGeneratedScene",
            "global_notes": "Contract smoke test.",
            "frames": [
                {
                    "id": f"frame_{index}",
                    "speech_segment_id": sid,
                    "start": start,
                    "end": end,
                    "title": f"Frame {index}",
                    "scene_goal": text,
                    "layout_notes": "Use a moving marker and arrows.",
                    "duration_sec": 4,
                    "code": PARALLEA_SCENE,
                }
                for index, (sid, start, end, text) in enumerate(segments, start=1)
            ],
        },
    }


async def main() -> None:
    question_pipeline.call_openai_manim_pipeline = fake_openai_manim_pipeline
    blueprint = await question_pipeline.build_question_pipeline(
        question="Explain cause and effect visually.",
        context="No uploaded video context is available, so teach from the persona style.",
        title="Persona-only contract test",
        learner_request="Teach this without a source video.",
        persona_context="Patient teacher persona.",
        preferred_visualization="manim",
    )
    frame = await materialize_frame_plan((blueprint.get("frame_sequence") or [])[0])
    media_url = ((frame.get("payload") or {}).get("media_url") or (frame.get("render_output") or {}).get("media_url") or "")
    media_path = (frame.get("render_output") or {}).get("media_path") or ""
    result = {
        "answerChars": len(blueprint.get("answer") or ""),
        "teachingSegments": len(blueprint.get("teaching_segments") or []),
        "frameRenderMode": frame.get("render_mode"),
        "sceneClassName": ((frame.get("render_output") or {}).get("payload") or {}).get("scene_class_name"),
        "mediaUrl": media_url,
        "mediaPath": media_path,
        "outputExists": bool(media_path and Path(media_path).exists()),
        "servedByRenderedScenes": media_url.startswith("/rendered-scenes/manim/"),
        "flatMp4Output": bool(media_path and Path(media_path).name.endswith(".mp4") and Path(media_path).parent.name == "manim"),
        "usedFallback": bool((frame.get("render_output") or {}).get("used_fallback")),
    }
    print(json.dumps(result, indent=2))
    assert result["teachingSegments"] == 3
    assert result["frameRenderMode"] == "manim"
    assert result["sceneClassName"] == "ParalleaGeneratedScene"
    assert result["outputExists"]
    assert result["servedByRenderedScenes"]
    assert result["flatMp4Output"]


if __name__ == "__main__":
    asyncio.run(main())
