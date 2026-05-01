"""Render a text-only Manim scene end-to-end and assert no LaTeX is required.

Usage:
    python -m backend.scripts.test_manim_no_latex

Works on Windows even when LaTeX is not installed. Prints a JSON summary with
the output mp4 path and the browser-safe public URL.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import manim_renderer


SAFE_TEXT_ONLY_SCENE = '''from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("No-LaTeX Manim Render", font_size=42)
        eq = Text("v = u + a t", font_size=34)
        line = Text("Range = u^2 sin(2 theta) / g", font_size=28)
        group = VGroup(title, eq, line).arrange(DOWN, buff=0.45)
        self.play(Write(title))
        self.play(FadeIn(eq, shift=UP * 0.2))
        self.play(FadeIn(line, shift=UP * 0.2))
        box = Rectangle(width=11, height=4.6, color=BLUE)
        self.play(Create(box))
        self.wait(1)
'''

MATHTEX_SCENE_SHOULD_FALL_BACK = '''from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("This should use fallback", font_size=36)
        equation = MathTex(r"v = u + at")
        equation.next_to(title, DOWN)
        self.play(Write(title))
        self.play(Write(equation))
        self.wait(1)
'''


def main() -> int:
    original_has_latex = manim_renderer.has_latex_available
    manim_renderer.has_latex_available = lambda: False
    try:
        manim_renderer.manim_runtime_info.cache_clear()
    except Exception:
        pass
    try:
        runtime = manim_renderer.manim_runtime_info()
        summary = {
            "pythonExecutable": runtime.get("python_executable") or sys.executable,
            "manimVersion": runtime.get("manim_version"),
            "latexAvailableSimulated": manim_renderer.has_latex_available(),
            "textOnlyMode": manim_renderer.manim_text_only_mode(),
        }
        run_id = uuid.uuid4().hex[:8]
        payload = {
            "renderer_version": manim_renderer.DIRECT_MANIM_RENDERER_VERSION,
            "scene_class_name": manim_renderer.DIRECT_SCENE_CLASS_NAME,
            "manim_code": SAFE_TEXT_ONLY_SCENE,
            "title": "No-LaTeX Manim Render",
            "subtitle": f"Text-only renderer smoke test {run_id}",
            "duration_sec": 6,
            "segment_id": f"no_latex_smoke_test_{run_id}",
        }
        rendered = manim_renderer.render_manim_payload(payload, segment_id=f"no_latex_smoke_test_{run_id}", frame_number=1)
        media_url = rendered.get("video_url") or rendered.get("media_url")
        media_path = rendered.get("media_path")

        fallback_payload = {
            "renderer_version": manim_renderer.DIRECT_MANIM_RENDERER_VERSION,
            "scene_class_name": manim_renderer.DIRECT_SCENE_CLASS_NAME,
            "manim_code": MATHTEX_SCENE_SHOULD_FALL_BACK,
            "title": "No-LaTeX fallback render",
            "subtitle": f"MathTex rejection smoke test {run_id}",
            "duration_sec": 6,
            "segment_id": f"no_latex_fallback_{run_id}",
        }
        fallback_rendered = manim_renderer.render_manim_payload(
            fallback_payload,
            segment_id=f"no_latex_fallback_{run_id}",
            frame_number=2,
        )
        fallback_path = fallback_rendered.get("media_path")
        summary.update(
            {
                "status": "ok" if media_path and Path(media_path).exists() else "failed",
                "publicUrl": media_url,
                "outputPath": media_path,
                "outputExists": bool(media_path and Path(media_path).exists()),
                "usedFallback": bool(rendered.get("used_fallback")),
                "mathtexRejectedAndFallbackRendered": bool(
                    fallback_rendered.get("used_fallback")
                    and fallback_path
                    and Path(fallback_path).exists()
                ),
                "fallbackPublicUrl": fallback_rendered.get("video_url") or fallback_rendered.get("media_url"),
            }
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] == "ok" and summary["mathtexRejectedAndFallbackRendered"] else 1
    finally:
        manim_renderer.has_latex_available = original_has_latex
        try:
            manim_renderer.manim_runtime_info.cache_clear()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
