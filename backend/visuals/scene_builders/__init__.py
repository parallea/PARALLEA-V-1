from __future__ import annotations

from typing import Any, Callable

from ..storyboard_schema import ScenePlan, Storyboard
from .biology_builder import build_biology_scene
from .chemistry_builder import build_chemistry_scene
from .cs_builder import build_cs_scene
from .generic_builder import build_generic_scene
from .math_builder import build_math_scene
from .physics_builder import build_physics_scene


BuilderFn = Callable[[ScenePlan, Storyboard, dict[str, Any]], dict[str, Any]]

BUILDERS: dict[str, BuilderFn] = {
    "math": build_math_scene,
    "physics": build_physics_scene,
    "biology": build_biology_scene,
    "chemistry": build_chemistry_scene,
    "cs": build_cs_scene,
    "generic": build_generic_scene,
}


def build_scene_render_payload(scene: ScenePlan, storyboard: Storyboard, context: dict[str, Any]) -> dict[str, Any]:
    subject = (storyboard.subject or context.get("subject") or "generic").strip().lower()
    builder = BUILDERS.get(subject, build_generic_scene)
    return builder(scene, storyboard, context)

