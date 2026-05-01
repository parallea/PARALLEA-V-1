"""Validate bad generated Manim code is rejected before render and fallback renders.

Usage:
    python -m backend.scripts.test_manim_bad_code_fallback
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import manim_renderer


def generated_scene(body: str) -> str:
    return f"""from manim import *

class GeneratedScene(Scene):
    def construct(self):
{body}
"""


def main() -> int:
    original_has_latex = manim_renderer.has_latex_available
    manim_renderer.has_latex_available = lambda: False
    try:
        manim_renderer.manim_runtime_info.cache_clear()
    except Exception:
        pass
    try:
        run_id = uuid.uuid4().hex[:8]
        bad_color = generated_scene(
            '        dot = Dot(color=Color(hsl=(0.3, 1.0, 0.5)))\n'
            '        self.play(FadeIn(dot))\n'
            '        self.wait(1)\n'
        )
        bad_mathtex = generated_scene(
            '        equation = MathTex(r"v = u + at")\n'
            '        self.play(Write(equation))\n'
            '        self.wait(1)\n'
        )
        unsafe_import = """from manim import *
import os

class GeneratedScene(Scene):
    def construct(self):
        self.play(Write(Text("unsafe")))
"""
        checks = {
            "Color(hsl=...)": manim_renderer.direct_manim_validation_error(bad_color),
            "MathTex when LaTeX disabled": manim_renderer.direct_manim_validation_error(bad_mathtex),
            "unsafe imports": manim_renderer.direct_manim_validation_error(unsafe_import),
        }
        payload = {
            "renderer_version": manim_renderer.DIRECT_MANIM_RENDERER_VERSION,
            "scene_class_name": manim_renderer.DIRECT_SCENE_CLASS_NAME,
            "manim_code": bad_color,
            "title": f"Bad-code fallback {run_id}",
            "subtitle": "Validator should reject unsupported color code.",
            "duration_sec": 5,
            "segment_id": f"bad_code_fallback_{run_id}",
        }
        rendered = manim_renderer.render_manim_payload(payload, segment_id=f"bad_code_fallback_{run_id}", frame_number=1)
        media_path = rendered.get("media_path")
        output = {
            "pythonExecutable": manim_renderer.manim_runtime_info().get("python_executable") or sys.executable,
            "manimVersion": manim_renderer.manim_runtime_info().get("manim_version"),
            "latexAvailableSimulated": manim_renderer.has_latex_available(),
            "validationErrors": checks,
            "allBadCodeRejected": all(bool(value) for value in checks.values()),
            "fallbackRendered": bool(rendered.get("used_fallback") and media_path and Path(media_path).exists()),
            "publicUrl": rendered.get("video_url") or rendered.get("media_url"),
            "outputPath": media_path,
        }
        print(json.dumps(output, indent=2))
        return 0 if output["allBadCodeRejected"] and output["fallbackRendered"] else 1
    finally:
        manim_renderer.has_latex_available = original_has_latex
        try:
            manim_renderer.manim_runtime_info.cache_clear()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
