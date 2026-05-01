"""End-to-end clarification visual smoke test.

Simulates: persona prompt + roadmap part context + student doubt + speech text +
visual prompt/code, then renders Manim and prints the final videoUrl.

Usage:
    python -m backend.scripts.test_clarification_manim

Works without LaTeX and without a running web server.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from manim_renderer import (
    DIRECT_MANIM_RENDERER_VERSION,
    DIRECT_SCENE_CLASS_NAME,
    has_latex_available,
    manim_runtime_info,
    manim_text_only_mode,
)
from backend.visuals.manim_renderer import render_manim_payload_async


SIMULATED_LLM_MANIM_CODE = '''from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Clarification: projectile range", font_size=38, color=WHITE)
        title.to_edge(UP, buff=0.5)
        eq = Text("Range = u^2 sin(2 theta) / g", font_size=30, color=YELLOW)
        eq.next_to(title, DOWN, buff=0.5)
        bullets = VGroup(
            Text("1. The launch speed u sets the energy.", font_size=24),
            Text("2. The angle theta sets the spread between height and distance.", font_size=24),
            Text("3. Gravity g pulls everything down at the same rate.", font_size=24),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.3)
        bullets.next_to(eq, DOWN, buff=0.6)
        box = Rectangle(width=12, height=2.8, color=BLUE).move_to(bullets)
        self.play(Write(title))
        self.play(FadeIn(eq, shift=UP * 0.2))
        self.play(Create(box), FadeIn(bullets, shift=UP * 0.2))
        self.wait(1.2)
'''


def simulated_clarification_payload() -> dict:
    return {
        "personaPrompt": "Confident, patient physics teacher who breaks things into mechanical steps.",
        "roadmapPart": {
            "id": "part_demo_1",
            "title": "Projectile motion: range formula",
            "summary": "Derive how the horizontal range depends on launch angle and speed.",
            "concepts": ["projectile motion", "trigonometry", "kinematics"],
            "equations": ["v = u + a t", "Range = u^2 sin(2 theta) / g"],
            "suggested_visuals": ["plot of range vs angle"],
            "transcript_chunk": "Earlier in this part the teacher set up u, theta, and g.",
        },
        "studentDoubt": "I did not understand why the angle 45 degrees gives the maximum range.",
        "speechText": (
            "Let's slow down on the range formula. Range equals u squared sine 2 theta over g. "
            "When theta is 45 degrees, sine 2 theta hits its peak of one, so range peaks too."
        ),
        "manimCode": SIMULATED_LLM_MANIM_CODE,
    }


async def run_async() -> dict:
    fixture = simulated_clarification_payload()
    runtime = manim_runtime_info()
    summary = {
        "pythonExecutable": runtime.get("python_executable") or sys.executable,
        "manimVersion": runtime.get("manim_version"),
        "latexAvailable": has_latex_available(),
        "textOnlyMode": manim_text_only_mode(),
        "doubt": fixture["studentDoubt"],
        "speechChars": len(fixture["speechText"]),
        "manimCodeChars": len(fixture["manimCode"]),
    }
    renderer_payload = {
        "renderer_version": DIRECT_MANIM_RENDERER_VERSION,
        "scene_class_name": DIRECT_SCENE_CLASS_NAME,
        "manim_code": fixture["manimCode"],
        "title": fixture["roadmapPart"]["title"],
        "subtitle": fixture["roadmapPart"]["summary"],
        "duration_sec": 8,
        "segment_id": "clarification_demo",
    }
    rendered = await render_manim_payload_async(
        renderer_payload, segment_id="clarification_demo", frame_number=1
    )
    media_url = rendered.get("video_url") or rendered.get("media_url")
    media_path = rendered.get("media_path")
    summary.update(
        {
            "status": "ok" if media_path and Path(media_path).exists() else "failed",
            "videoUrl": media_url,
            "outputPath": media_path,
            "outputExists": bool(media_path and Path(media_path).exists()),
            "usedFallback": bool(rendered.get("used_fallback")),
            "renderLog": rendered.get("render_log_path"),
        }
    )
    return summary


def main() -> int:
    summary = asyncio.run(run_async())
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
