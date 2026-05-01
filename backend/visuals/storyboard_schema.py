from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def trim_sentence(text: Any, limit: int = 160) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _normalize_text_list(items: Any, *, limit: int, item_limit: int) -> list[str]:
    values: list[str] = []
    for item in items or []:
        cleaned = trim_sentence(item, item_limit)
        if cleaned:
            values.append(cleaned)
        if len(values) >= limit:
            break
    return values


@dataclass(slots=True)
class ScenePlan:
    scene_id: str
    scene_goal: str
    scene_type: str
    key_visual_objects: list[str] = field(default_factory=list)
    animation_flow: list[str] = field(default_factory=list)
    text_usage: str = "minimal labels only"
    equations_usage: str = "none"
    transitions: str = "smooth visual carry from the previous scene"
    emphasis_points: list[str] = field(default_factory=list)
    estimated_duration: float = 6.0
    layout_hint: str = "center_morph"
    camera_behavior: str = "steady framing with local focus shifts"
    pedagogical_role: str = "intuition"
    segment_ref: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, fallback_id: str = "scene_1") -> "ScenePlan":
        return cls(
            scene_id=clean_spaces(raw.get("scene_id")) or fallback_id,
            scene_goal=trim_sentence(raw.get("scene_goal"), 220) or "Build the core idea visually.",
            scene_type=clean_spaces(raw.get("scene_type")).lower() or "concept_metaphor",
            key_visual_objects=_normalize_text_list(raw.get("key_visual_objects"), limit=6, item_limit=48),
            animation_flow=_normalize_text_list(raw.get("animation_flow"), limit=6, item_limit=140),
            text_usage=trim_sentence(raw.get("text_usage") or "minimal labels only", 80),
            equations_usage=trim_sentence(raw.get("equations_usage") or "none", 80),
            transitions=trim_sentence(raw.get("transitions") or "smooth visual carry from the previous scene", 120),
            emphasis_points=_normalize_text_list(raw.get("emphasis_points"), limit=5, item_limit=60),
            estimated_duration=round(clamp(safe_float(raw.get("estimated_duration"), 6.0), 3.5, 12.0), 1),
            layout_hint=clean_spaces(raw.get("layout_hint")).lower() or "center_morph",
            camera_behavior=trim_sentence(raw.get("camera_behavior") or "steady framing with local focus shifts", 100),
            pedagogical_role=clean_spaces(raw.get("pedagogical_role")).lower() or "intuition",
            segment_ref=clean_spaces(raw.get("segment_ref")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "scene_goal": self.scene_goal,
            "scene_type": self.scene_type,
            "key_visual_objects": list(self.key_visual_objects),
            "animation_flow": list(self.animation_flow),
            "text_usage": self.text_usage,
            "equations_usage": self.equations_usage,
            "transitions": self.transitions,
            "emphasis_points": list(self.emphasis_points),
            "estimated_duration": self.estimated_duration,
            "layout_hint": self.layout_hint,
            "camera_behavior": self.camera_behavior,
            "pedagogical_role": self.pedagogical_role,
            "segment_ref": self.segment_ref,
        }


@dataclass(slots=True)
class Storyboard:
    concept_summary: str
    teaching_goal: str
    visual_strategy: str
    pacing_style: str
    scene_sequence: list[ScenePlan] = field(default_factory=list)
    subject: str = ""
    requested_depth: str = "normal"
    preferred_style: str = ""
    quality_scores: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        *,
        subject: str = "",
        requested_depth: str = "normal",
        preferred_style: str = "",
    ) -> "Storyboard":
        scenes: list[ScenePlan] = []
        for index, item in enumerate(raw.get("scene_sequence") or [], start=1):
            if isinstance(item, dict):
                scenes.append(ScenePlan.from_dict(item, fallback_id=f"scene_{index}"))
        if not scenes:
            scenes = [
                ScenePlan(
                    scene_id="scene_1",
                    scene_goal="Introduce the core concept with a strong visual anchor.",
                    scene_type="concept_metaphor",
                    key_visual_objects=["core concept", "one changing reference"],
                    animation_flow=["Reveal the anchor object", "Show the core change rather than writing the answer"],
                    emphasis_points=["The first visual should make the idea feel graspable"],
                )
            ]
        return cls(
            concept_summary=trim_sentence(raw.get("concept_summary"), 240) or "Teach the underlying concept with visual intuition first.",
            teaching_goal=trim_sentence(raw.get("teaching_goal"), 220) or "Help the learner understand the concept before formal notation.",
            visual_strategy=trim_sentence(raw.get("visual_strategy"), 180) or "Use motion, comparison, and transformation before formal text.",
            pacing_style=trim_sentence(raw.get("pacing_style"), 120) or "Varied rhythm with a calm introduction and stronger formal payoff later.",
            scene_sequence=scenes,
            subject=clean_spaces(raw.get("subject")) or subject,
            requested_depth=clean_spaces(raw.get("requested_depth")) or requested_depth,
            preferred_style=trim_sentence(raw.get("preferred_style"), 80) or preferred_style,
            quality_scores={
                clean_spaces(key): round(clamp(safe_float(value, 0.0), 0.0, 1.0), 3)
                for key, value in (raw.get("quality_scores") or {}).items()
                if clean_spaces(key)
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_summary": self.concept_summary,
            "teaching_goal": self.teaching_goal,
            "visual_strategy": self.visual_strategy,
            "pacing_style": self.pacing_style,
            "scene_sequence": [scene.to_dict() for scene in self.scene_sequence],
            "subject": self.subject,
            "requested_depth": self.requested_depth,
            "preferred_style": self.preferred_style,
            "quality_scores": dict(self.quality_scores),
        }


def storyboard_response_schema() -> dict[str, Any]:
    scene_schema = {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string"},
            "scene_goal": {"type": "string"},
            "scene_type": {"type": "string"},
            "key_visual_objects": {"type": "array", "items": {"type": "string"}},
            "animation_flow": {"type": "array", "items": {"type": "string"}},
            "text_usage": {"type": "string"},
            "equations_usage": {"type": "string"},
            "transitions": {"type": "string"},
            "emphasis_points": {"type": "array", "items": {"type": "string"}},
            "estimated_duration": {"type": "number"},
            "layout_hint": {"type": "string"},
            "camera_behavior": {"type": "string"},
            "pedagogical_role": {"type": "string"},
            "segment_ref": {"type": "string"},
        },
        "required": [
            "scene_id",
            "scene_goal",
            "scene_type",
            "key_visual_objects",
            "animation_flow",
            "text_usage",
            "equations_usage",
            "transitions",
            "emphasis_points",
            "estimated_duration",
            "layout_hint",
            "camera_behavior",
            "pedagogical_role",
        ],
    }
    return {
        "type": "object",
        "properties": {
            "concept_summary": {"type": "string"},
            "teaching_goal": {"type": "string"},
            "visual_strategy": {"type": "string"},
            "pacing_style": {"type": "string"},
            "scene_sequence": {"type": "array", "items": scene_schema},
        },
        "required": [
            "concept_summary",
            "teaching_goal",
            "visual_strategy",
            "pacing_style",
            "scene_sequence",
        ],
    }

