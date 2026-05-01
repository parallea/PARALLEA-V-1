from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from manim_renderer import (
    has_latex_available,
    latex_runtime_info,
    manim_allow_mathtex_effective_value,
    manim_runtime_info,
    render_manim_healthcheck,
)


def main() -> int:
    runtime = manim_runtime_info()
    result = render_manim_healthcheck()
    latex_info = latex_runtime_info()
    output = {
        "manim_version": runtime.get("manim_version"),
        "python_executable": runtime.get("python_executable") or sys.executable,
        "working_directory": os.getcwd(),
        "latex_available": has_latex_available(),
        "latex_path": latex_info.get("latex_path") or "",
        "dvisvgm_available": bool(latex_info.get("dvisvgm_available")),
        "dvisvgm_path": latex_info.get("dvisvgm_path") or "",
        "MANIM_ALLOW_MATHTEX_effective": manim_allow_mathtex_effective_value(),
        "text_scene_rendered": bool(result.get("text_scene_rendered")),
        "mathtex_scene_rendered": bool(result.get("mathtex_scene_rendered")),
        "mathtex_scene_skipped": bool(result.get("mathtex_scene_skipped")),
        "mathtex_scene_skipped_reason": result.get("mathtex_scene_skipped_reason") or "",
        "text_scene_url": (result.get("text_scene") or {}).get("media_url") or result.get("media_url"),
        "text_scene_path": (result.get("text_scene") or {}).get("media_path") or result.get("media_path"),
        "mathtex_scene_url": ((result.get("mathtex_scene") or {}).get("media_url") if result.get("mathtex_scene") else None),
        "mathtex_scene_path": ((result.get("mathtex_scene") or {}).get("media_path") if result.get("mathtex_scene") else None),
        "mathtex_error": result.get("mathtex_error") or "",
    }
    if output["text_scene_path"]:
        output["text_scene_exists"] = Path(output["text_scene_path"]).exists()
    print(json.dumps(output, indent=2))
    return 0 if output["text_scene_rendered"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
