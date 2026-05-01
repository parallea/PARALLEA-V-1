"""Answer generation for the immersive learning flow.

Two modes, mirroring the spec's two answer prompts:

  video_context  — student topic IS in an uploaded roadmap. Use the part
                   transcript chunk + concepts/equations/examples as the
                   primary source. Persona prompt sets the voice.

  persona_only   — student topic is NOT in uploaded videos. Persona prompt
                   sets the voice. Answer must NOT claim it came from
                   uploaded videos. Includes a `disclaimer` field.

Both return the same JSON shape consumed by the immersive UI:
  {
    "spokenAnswer": str,
    "shortSummary": str,         # video_context only
    "disclaimer":   str,         # persona_only only
    "visualNeeded": bool,
    "visualType":   "manim"|"tldraw"|"none",
    "visualPrompt": str,
    "askFollowUp":  str,
  }

Reuses the same provider-detection + stub fallback as
`backend/services/persona_pipeline.py`.
"""
from __future__ import annotations

import logging
import json
import math
import re
from typing import Any, Optional

from config import MANIM_VISUAL_STYLE, MAX_MANIM_VISUAL_DURATION_SECONDS
from backend.services.model_router import get_model_config, llm_json
from manim_renderer import (
    direct_manim_validation_error,
    has_latex_available,
    manim_allow_mathtex_effective_value,
    manim_mathtex_allowed,
    manim_text_only_mode,
)

logger = logging.getLogger("parallea.answer_service")

PART_UNDERSTANDING_QUESTION = "Is there anything in this part that you didn't understand?"
CLARIFICATION_FOLLOWUP = "Does that make sense now?"
MANIM_SCENE_CLASS_NAME = "GeneratedScene"

_MANIM_REGION_HELPERS_CODE = '''config.frame_width = 14.222
config.frame_height = 8.0
config.pixel_width = 1280
config.pixel_height = 720

SAFE_MARGIN = 0.35
FRAME_WIDTH = 14.222
FRAME_HEIGHT = 8.0
REGION_CENTERS = {
    "title": UP * 3.25,
    "left": LEFT * 3.35 + UP * 0.05,
    "right": RIGHT * 3.25 + UP * 0.05,
    "bottom": DOWN * 3.25,
}
REGION_SIZES = {
    "title": (12.2, 0.9),
    "left": (5.6, 5.0),
    "right": (5.6, 5.0),
    "bottom": (12.0, 0.75),
}

def fit_to_region(mobject, max_width, max_height):
    if mobject.width > max_width:
        mobject.scale_to_fit_width(max_width)
    if mobject.height > max_height:
        mobject.scale_to_fit_height(max_height)
    return mobject

def keep_inside_frame(mobject):
    half_w = FRAME_WIDTH / 2 - SAFE_MARGIN
    half_h = FRAME_HEIGHT / 2 - SAFE_MARGIN
    if mobject.width > half_w * 2:
        mobject.scale_to_fit_width(half_w * 2)
    if mobject.height > half_h * 2:
        mobject.scale_to_fit_height(half_h * 2)
    dx = 0
    dy = 0
    if mobject.get_right()[0] > half_w:
        dx -= mobject.get_right()[0] - half_w
    if mobject.get_left()[0] < -half_w:
        dx += -half_w - mobject.get_left()[0]
    if mobject.get_top()[1] > half_h:
        dy -= mobject.get_top()[1] - half_h
    if mobject.get_bottom()[1] < -half_h:
        dy += -half_h - mobject.get_bottom()[1]
    if dx or dy:
        mobject.shift(RIGHT * dx + UP * dy)
    return mobject

def safe_text(text, font_size=32, max_width=5.5):
    mobject = Text(str(text or ""), font_size=min(int(font_size), 48), color=WHITE)
    fit_to_region(mobject, max_width, 1.0)
    return mobject

def bullet_list(items, max_width=5.5, font_size=28):
    rows = VGroup()
    for item in list(items or [])[:5]:
        row = safe_text("- " + str(item), font_size=font_size, max_width=max_width)
        rows.add(row)
    if len(rows):
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.18)
    fit_to_region(rows, max_width, 4.7)
    return rows

def _place_region(mobject, region_name):
    max_width, max_height = REGION_SIZES[region_name]
    fit_to_region(mobject, max_width, max_height)
    mobject.move_to(REGION_CENTERS[region_name])
    keep_inside_frame(mobject)
    return mobject

def place_title(mobject):
    return _place_region(mobject, "title")

def place_left(mobject):
    return _place_region(mobject, "left")

def place_right(mobject):
    return _place_region(mobject, "right")

def place_bottom(mobject):
    return _place_region(mobject, "bottom")

def clear_region(scene, active_regions, region_name):
    old_mobject = active_regions.get(region_name)
    if old_mobject is not None:
        scene.play(FadeOut(old_mobject), run_time=0.4)
        active_regions[region_name] = None

def replace_region(scene, active_regions, region_name, new_mobject, animation=FadeIn):
    clear_region(scene, active_regions, region_name)
    scene.play(animation(new_mobject), run_time=0.6)
    active_regions[region_name] = new_mobject
    return new_mobject

def clear_all_regions(scene, active_regions, keep_title=False):
    for region_name in list(active_regions.keys()):
        if keep_title and region_name == "title":
            continue
        clear_region(scene, active_regions, region_name)
'''

_MANIM_CREATIVE_SAFE_RULES = """Creative Safe Mode:
- Create a polished educational animation, not a slide deck.
- Use creative visual metaphors when helpful: symbolic characters, data points, gears, factories, paths, cards, matrices, maps, or simple story objects drawn from Manim primitives.
- Prefer smooth transformations, object movement, flowing arrows, highlight pulses, comparison diagrams, morphing shapes, and cause-effect motion.
- Do not force every concept into rigid Step 1 / Step 2 panels.
- Do not show generic visible labels like "Step 1", "Step 2", "Segment 1", or "Visual Step 1".
- Use short meaningful labels instead, such as "Image as numbers", "Feature vector", "Matrix transformation", "Prediction", "Loss decreases", or "Pattern learned".
- Keep the animation readable and uncluttered. Avoid long paragraphs and too many bullet points.
- Keep important objects inside a 16:9 frame with comfortable margins; do not place text near extreme edges.
- Fade out or transform old objects before placing new objects in the same area.
- Use named variables for important groups instead of fragile hardcoded indexes like hierarchy[7].
- Avoid arbitrary extreme shift values; use arrange(), next_to(), move_to(), align_to(), to_edge(..., buff=0.3), and scale_to_fit_width() when useful.
- Draw creative metaphors with built-in Manim primitives only. Do not use ImageMobject, SVGMobject, or external asset files.
- The final result should feel like a visual explainer video, not a static text board."""

_MANIM_STRICT_LAYOUT_RULES = """Strict layout mode:
- Use a 16:9 safe frame with SAFE_MARGIN = 0.35.
- Keep all important objects inside the safe margin; never place text or diagrams on extreme edges.
- Use fixed regions: title_region at the top, left_panel for bullets/equations, right_panel for diagrams or creative metaphors, and bottom_region for short takeaways/progress cues.
- Use the helper functions `safe_text`, `bullet_list`, `place_title`, `place_left`, `place_right`, `place_bottom`, `replace_region`, `clear_region`, and `clear_all_regions`.
- Use this active region pattern in every scene:
  active_regions = {"title": None, "left": None, "right": None, "bottom": None}
  place_left(new_left)
  replace_region(self, active_regions, "left", new_left)
- If a new object appears in an occupied region, fade out or transform the previous group first. Do not stack new objects on old ones.
- Use `FadeOut(old_group)` before `FadeIn(new_group)` when reusing the same region.
- Prefer VGroup.arrange(), next_to(), align_to(), move_to(), and the predefined place_* helpers. Avoid arbitrary large shift values.
- Keep text short. Split long explanations into multiple small Text objects. Use max 3-5 visible text items at once.
- Scale text and diagrams to fit their region before showing them. Never let text exceed frame width.
- Build step by step. Fade out completed steps before moving to the next concept.
- Keep visual metaphors creative, but draw them with built-in Manim primitives only. Do not use ImageMobject, SVGMobject, or external assets.
- Use SurroundingRectangle and arrows sparingly. Do not overlap labels with diagrams."""

_MANIM_SAFE_LAYOUT_RULES = _MANIM_STRICT_LAYOUT_RULES

_MANIM_REGION_HELPERS_PROMPT = f"""Include this helper block exactly after `from manim import *` in generated Manim files, then use it for placement.
Helper block:
{_MANIM_REGION_HELPERS_CODE}
End helper block."""


def _manim_visual_style() -> str:
    style = str(MANIM_VISUAL_STYLE or "creative_safe").strip().lower()
    return style if style in {"creative_safe", "strict_layout", "fallback_only"} else "creative_safe"


def _manim_generation_style_prompt() -> str:
    style = _manim_visual_style()
    if style == "strict_layout":
        return "\n\n".join([_MANIM_STRICT_LAYOUT_RULES, _MANIM_REGION_HELPERS_PROMPT])
    if style == "fallback_only":
        return "\n\n".join(
            [
                "MANIM_VISUAL_STYLE=fallback_only is active. Prefer simple reliable visuals; the renderer may use local fallback scenes.",
                _MANIM_STRICT_LAYOUT_RULES,
                _MANIM_REGION_HELPERS_PROMPT,
            ]
        )
    return _MANIM_CREATIVE_SAFE_RULES


def _manim_repair_style_prompt() -> str:
    style = _manim_visual_style()
    if style == "strict_layout":
        return "\n\n".join([_MANIM_STRICT_LAYOUT_RULES, _MANIM_REGION_HELPERS_PROMPT])
    if style == "fallback_only":
        return "\n\n".join([_MANIM_STRICT_LAYOUT_RULES, _MANIM_REGION_HELPERS_PROMPT])
    return """Creative Safe repair rules:
- Repair the exact validation or render failure while preserving the original visual metaphor and animation sequence.
- Do not replace the scene with generic cards unless the original idea cannot be made executable.
- Remove missing external assets by drawing an equivalent object with Manim primitives.
- Replace fragile hardcoded indexes such as hierarchy[7] with named variables or loops.
- Reduce offscreen shifts, add buff to edge placement when needed, and scale large text or diagrams down.
- If an object appears where another object already exists, FadeOut or Transform the old group first.
- If duration is too short, extend the same creative sequence across all spoken segments; do not add visible Step 1 / Step 2 labels."""


def _clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def _trim_sentence(text: Any, limit: int = 160) -> str:
    value = _clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _split_sentences(text: Any, limit: int = 4) -> list[str]:
    raw = _clean_spaces(text)
    if not raw:
        return []
    parts = [part.strip() for part in raw.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    return [_trim_sentence(part, 180) for part in parts[:limit]]


def _word_count(text: Any) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(text or "")))


def estimate_spoken_duration_seconds(text: Any) -> float:
    """Estimate speech duration using ~140 words/minute."""
    words = _word_count(text)
    if words <= 0:
        return 0.0
    return round((words / 140.0) * 60.0, 2)


def _target_visual_duration_seconds(estimated_spoken_duration: float) -> float:
    if estimated_spoken_duration <= 0:
        return 12.0
    return round(max(8.0, min(float(MAX_MANIM_VISUAL_DURATION_SECONDS), estimated_spoken_duration)), 2)


def _split_spoken_text_for_segments(text: Any, max_segments: int = 12) -> list[str]:
    raw = _clean_spaces(text)
    if not raw:
        return []
    candidates = [part.strip() for part in re.split(r"(?<=[.!?])\s+", raw) if part.strip()]
    if not candidates:
        words = raw.split()
        candidates = [" ".join(words[index:index + 24]) for index in range(0, len(words), 24)]
    expanded: list[str] = []
    for candidate in candidates:
        words = candidate.split()
        if len(words) <= 32:
            expanded.append(candidate)
            continue
        expanded.extend(" ".join(words[index:index + 24]) for index in range(0, len(words), 24))
    if len(expanded) <= max_segments:
        return expanded
    group_size = math.ceil(len(expanded) / max_segments)
    grouped = [" ".join(expanded[index:index + group_size]) for index in range(0, len(expanded), group_size)]
    return grouped[:max_segments]


def _scale_durations_to_target(durations: list[float], target_duration: float) -> list[float]:
    if not durations:
        return []
    total = sum(max(0.1, value) for value in durations)
    if total <= 0:
        return [round(target_duration / len(durations), 2) for _ in durations]
    scale = target_duration / total
    scaled = [max(2.4, value * scale) for value in durations]
    scaled_total = sum(scaled)
    if scaled_total > target_duration and scaled_total > 0:
        shrink = target_duration / scaled_total
        scaled = [max(1.4, value * shrink) for value in scaled]
    return [round(value, 2) for value in scaled]


def _normalize_spoken_segments(raw_segments: Any, spoken_answer: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for index, item in enumerate(raw_segments, start=1):
            if not isinstance(item, dict):
                continue
            text = _clean_spaces(item.get("text") or item.get("spoken_text") or item.get("speechText"))
            if not text:
                continue
            duration = _safe_float(
                item.get("estimated_duration_seconds") or item.get("duration_seconds") or item.get("duration"),
                estimate_spoken_duration_seconds(text),
            )
            segments.append(
                {
                    "id": _clean_spaces(item.get("id")) or f"seg_{index}",
                    "text": text,
                    "estimated_duration_seconds": max(1.4, duration),
                    "matching_visual_step_id": _clean_spaces(item.get("matching_visual_step_id") or item.get("matchingVisualStepId")) or f"vis_{index}",
                }
            )
    answer_words = _word_count(spoken_answer)
    segment_words = sum(_word_count(segment.get("text")) for segment in segments)
    if segments and answer_words >= 40 and segment_words < answer_words * 0.75:
        logger.warning(
            "[av-sync] spoken_segments covered too little of spoken_answer; rebuilding segments answer_words=%s segment_words=%s",
            answer_words,
            segment_words,
        )
        segments = []
    if not segments:
        chunks = _split_spoken_text_for_segments(spoken_answer)
        for index, text in enumerate(chunks, start=1):
            segments.append(
                {
                    "id": f"seg_{index}",
                    "text": text,
                    "estimated_duration_seconds": max(2.4, estimate_spoken_duration_seconds(text)),
                    "matching_visual_step_id": f"vis_{index}",
                }
            )
    estimated_total = estimate_spoken_duration_seconds(spoken_answer)
    target = _target_visual_duration_seconds(estimated_total)
    durations = _scale_durations_to_target([segment["estimated_duration_seconds"] for segment in segments], target)
    cursor = 0.0
    for index, segment in enumerate(segments):
        duration = durations[index] if index < len(durations) else segment["estimated_duration_seconds"]
        segment["estimated_duration_seconds"] = round(duration, 2)
        segment["start"] = round(cursor, 2)
        segment["end"] = round(cursor + duration, 2)
        cursor += duration
    return segments


def _normalize_visual_steps(raw_steps: Any, spoken_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_items = raw_steps if isinstance(raw_steps, list) else []
    total = max(len(spoken_segments), len(raw_items))
    regions = ["left", "right", "bottom", "left", "right"]
    steps: list[dict[str, Any]] = []
    for index in range(total):
        raw = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
        speech = spoken_segments[min(index, len(spoken_segments) - 1)] if spoken_segments else {}
        step_id = _clean_spaces(raw.get("id")) or _clean_spaces(speech.get("matching_visual_step_id")) or f"vis_{index + 1}"
        description = _clean_spaces(raw.get("description") or raw.get("cue") or raw.get("visualCue") or raw.get("matches_spoken_text"))
        if not description:
            description = _trim_sentence(speech.get("text") or "Key idea", 120)
        duration = _safe_float(raw.get("duration_seconds") or raw.get("duration") or raw.get("estimated_duration_seconds"), speech.get("estimated_duration_seconds") or 3.0)
        start = _safe_float(raw.get("start"), speech.get("start") or sum(step["duration_seconds"] for step in steps))
        end = _safe_float(raw.get("end"), start + duration)
        if end <= start:
            end = start + duration
        duration = max(1.4, end - start)
        steps.append(
            {
                "id": step_id,
                "description": description,
                "duration_seconds": round(duration, 2),
                "region": _clean_spaces(raw.get("region")) or regions[index % len(regions)],
                "matching_spoken_segment_id": _clean_spaces(raw.get("matching_spoken_segment_id") or raw.get("matchesSpeechSegmentId")) or speech.get("id") or f"seg_{index + 1}",
                "start": round(start, 2),
                "end": round(start + duration, 2),
            }
        )
    target = sum(segment.get("estimated_duration_seconds", 0.0) for segment in spoken_segments)
    durations = _scale_durations_to_target([step["duration_seconds"] for step in steps], target) if target > 0 else []
    cursor = 0.0
    for index, step in enumerate(steps):
        duration = durations[index] if index < len(durations) else step["duration_seconds"]
        step["duration_seconds"] = round(duration, 2)
        step["start"] = round(cursor, 2)
        step["end"] = round(cursor + duration, 2)
        cursor += duration
    return steps


_VIDEO_CONTEXT_SYSTEM = """You are teaching as the given teacher persona, answering a student inside an immersive lesson.

Use the CURRENT VIDEO PART as the primary source of facts. Stay in the teacher's voice (the persona prompt). Be conversational and suited to voice output (no markdown). If the student asks for exam help, include the key equations, common question types, and things to remember. If a visualization would help, request it via visualNeeded/visualType/visualPrompt.

Return STRICT JSON only (no fences) with this shape:
{
  "spokenAnswer": "string",
  "shortSummary": "string",
  "visualNeeded": true,
  "visualType": "manim | tldraw | none",
  "visualPrompt": "string",
  "askFollowUp": "string"
}"""


_PERSONA_ONLY_SYSTEM = """You are teaching as the given teacher persona. The student asked about a topic that the teacher has NOT uploaded a video on. You can still teach in the teacher's style, but you MUST NOT claim this came from the teacher's uploaded video.

Set the `disclaimer` field to a brief acknowledgement that this topic isn't from the uploaded videos (e.g. "This topic isn't from the uploaded videos, but I can explain it in this teacher's style.").

Stay in the teacher's voice (the persona prompt). Be conversational and suited to voice output (no markdown). Use Manim visualization if useful.

Return STRICT JSON only (no fences) with this shape:
{
  "spokenAnswer": "string",
  "disclaimer": "string",
  "visualNeeded": true,
  "visualType": "manim | tldraw | none",
  "visualPrompt": "string",
  "askFollowUp": "string"
}"""


_TEACH_ROADMAP_PART_SYSTEM = """You are an AI teacher persona teaching in the style of the selected teacher.
You must stay grounded in the current roadmap part from the teacher's uploaded video.
Do not replace the lesson with a generic board explanation.
Speak naturally as a teacher.

Return STRICT JSON only (no fences) with this shape:
{
  "speechText": "string",
  "askFollowUp": "Is there anything in this part that you didn't understand?"
}"""


_CLARIFY_ROADMAP_PART_SYSTEM = """You are an AI teacher persona clarifying a student's doubt about the current roadmap part.
You must use the current video/roadmap part context as the primary source.
Return a spoken answer and a Manim visual explanation.

Return STRICT JSON only (no fences) with this shape:
{
  "speech": {
    "text": "string",
    "timestamps": [
      { "start": 0.0, "end": 3.2, "text": "string" }
    ]
  },
  "visual": {
    "visualNeeded": true,
    "visualType": "manim",
    "manimCode": "string",
    "timestamps": [
      { "start": 0.0, "end": 3.2, "cue": "string" }
    ]
  },
  "askFollowUp": "Does that make sense now?"
}

Manim code rules:
- Every manimCode value must be a full Python file.
- Use `from manim import *` as the only import.
- Declare exactly `class GeneratedScene(Scene):`.
- Do not use external files, network calls, shell commands, file I/O, exec, or eval.
- Keep text short enough to fit in a 16:9 frame.
- Prefer Text for equations unless the renderer-specific prompt explicitly allows MathTex/Tex.
- Do not use Color(...), ManimColor(...), hsl=, rgb_to_color, colour, or manim.utils.color.
- Use only built-in color constants: WHITE, BLACK, BLUE, BLUE_E, GREEN, GREEN_E, RED, RED_E, YELLOW, ORANGE, PURPLE, GREY, GRAY.
- Use reliable Manim primitives and keep all objects inside a 16:9 frame."""


_MANIM_VISUAL_SYSTEM_BASE = """You are a senior Python Manim Community Edition engineer.

Generate safe executable Manim code for a student clarification.

Rules:
- Output Python code only, inside the JSON `manimCode` string. No markdown fences.
- Use `from manim import *`.
- Define exactly one scene class named `GeneratedScene`.
- Do not use unsafe imports.
- Do not use external files/assets.
- Do not use network calls.
- Use reliable Manim primitives.
- Keep all objects inside frame.
- Use 16:9 layout.
- Must render using: `python -m manim -ql generated_scene.py GeneratedScene`.
- Do NOT use Color(...), ManimColor(...), hsl=, rgb_to_color, `from colour`, `import colour`, or `from manim.utils.color`.
- Use only built-in color constants: WHITE, BLACK, BLUE, BLUE_E, GREEN, GREEN_E, RED, RED_E, YELLOW, ORANGE, PURPLE, GREY, GRAY.
- In creative_safe mode, render the OpenAI scene directly when it passes hard safety checks; do not convert it into a rigid template.
- Keep the scene reliable, but make it visually engaging.
- Match the provided speech and visual cues. Use `self.play(...)` and `self.wait(...)` to pace the animation.

Return STRICT JSON only:
{
  "manimCode": "full Python file as a string"
}"""


def _manim_latex_prompt_rules(*, force_no_latex: bool = False, rejection_reason: str = "") -> str:
    if not force_no_latex and manim_mathtex_allowed():
        return """
LaTeX status:
- LaTeX is available and MANIM_ALLOW_MATHTEX permits it.
- You may use MathTex/Tex for equations only.
- Keep MathTex simple.
""".strip()
    reason = rejection_reason or manim_allow_mathtex_effective_value()
    return f"""
LaTeX status:
- LaTeX is unavailable or MathTex is disabled ({reason}).
- Do NOT use MathTex, Tex, or SingleStringMathTex.
- Use Text("v = u + at") style formulas.
""".strip()


def _manim_visual_system(*, force_no_latex: bool = False, rejection_reason: str = "") -> str:
    return "\n\n".join(
        [
            _MANIM_VISUAL_SYSTEM_BASE,
            _manim_generation_style_prompt(),
            _manim_latex_prompt_rules(force_no_latex=force_no_latex, rejection_reason=rejection_reason),
        ]
    )


_COMBINED_TEACHING_SYSTEM_BASE = """You are an expert real-time AI teacher and Manim animation director.

You must generate one synchronized teaching response from one reasoning plan:
1. spoken answer
2. compact teaching state update
3. visual plan with timestamps
4. executable Manim code
5. one follow-up question

The student is learning from a teacher persona. Preserve the teacher's style.

The output must be valid JSON only and must use this shape:
{
  "spoken_answer": "voice-friendly answer",
  "spoken_segments": [
    {
      "id": "seg_1",
      "text": "one spoken segment",
      "estimated_duration_seconds": 8,
      "matching_visual_step_id": "vis_1"
    }
  ],
  "visual_steps": [
    {
      "id": "vis_1",
      "description": "what appears or changes on screen",
      "duration_seconds": 8,
      "region": "left|right|title|bottom"
    }
  ],
  "estimated_total_visual_duration_seconds": 60,
  "teaching_state_update": {
    "current_topic": "string",
    "current_step": "string",
    "student_understanding_summary": "string",
    "unresolved_student_question": "string",
    "next_teaching_goal": "string"
  },
  "visual_plan_with_timestamps": [
    {
      "id": "vis_1",
      "start": 0.0,
      "end": 4.0,
      "matches_spoken_text": "short spoken phrase this visual supports",
      "description": "what appears or changes on screen"
    }
  ],
  "manim_code": "full Python file defining class GeneratedScene(Scene)",
  "follow_up_question": "string"
}

Duration and coverage rules:
- Keep spoken_answer around 45-70 seconds when possible. End with a brief follow-up question outside the answer field.
- The Manim animation must cover the entire spoken_answer, not just the introduction.
- If spoken_answer explains 6 steps, the Manim scene must show 6 visual steps.
- Do not stop after the first 20 seconds.
- Use waits and step-by-step transitions so the final MP4 duration is close to the spoken answer duration.
- Estimate spoken duration at about 140 words per minute.
- Target visual duration is the estimated spoken duration, capped at 90 seconds.
- Minimum acceptable visual duration is 75% of the estimated spoken duration.
- Every spoken segment must have a matching visual step.
- visual_steps count must be >= spoken_segments count.
- Sum of visual_steps durations should be close to total spoken duration.
- Manim code must implement every visual_step in order.
- Each spoken segment must correspond to a Manim animation or visual state.

The visual must be interactive and explanatory, not a static board.
Use step-by-step animations:
- reveal concepts progressively
- move arrows/dots/objects
- highlight key ideas
- transform examples
- show cause-effect
- use diagrams instead of walls of text

Creative quality rules:
- Create a polished educational animation, not a slide deck.
- Use creative visual metaphors when they help the explanation.
- Prefer smooth transformations, flowing arrows, highlight pulses, comparison diagrams, symbolic objects drawn from primitives, moving data points, and matrices/vectors transforming.
- Do not show generic visible labels like "Step 1", "Step 2", "Segment 1", or "Visual Step 1".
- Use short meaningful labels tied to the concept instead.
- Fade out or transform old objects before placing new objects in the same area.

Do not create a boring text board.
Do not create mostly static rectangles and text.

If this is a clarification for an uploaded video part, stay grounded in the video part context.
If this is persona-only teaching, explain in the teacher's style but do not claim the teacher uploaded this topic.

The spoken answer and Manim code must come from the same visual_plan_with_timestamps. Do not invent a separate visual explanation.

Manim code rules:
- use `from manim import *`
- define exactly one class named GeneratedScene
- be executable
- avoid unsafe imports
- avoid file/network access
- avoid unsupported constructors like Color(hsl=...)
- use built-in colors only
- avoid MathTex/Tex unless LaTeX is available
- use Text for equations if LaTeX unavailable
- keep runtime at or below 90 seconds
- prefer one clear animation/state per spoken segment over a short intro-only scene
- use progressive animation, not a static board

Allowed primitives:
- Text
- MarkupText
- Rectangle
- RoundedRectangle
- Circle
- Ellipse
- Arrow
- Line
- Dot
- VGroup
- Axes
- NumberPlane
- Create
- Write
- FadeIn
- FadeOut
- Transform
- Indicate
- Circumscribe

Forbidden unless explicitly supported:
- Color(...)
- ManimColor(...)
- MathTex/Tex when LaTeX unavailable
- external assets
- images
- network/file operations
- unsafe imports
"""


_MANIM_REPAIR_SYSTEM = """You are a senior Python Manim Community Edition engineer repairing one generated scene.

Return STRICT JSON only:
{
  "manim_code": "full Python file defining class GeneratedScene(Scene)"
}

Rules:
- use `from manim import *` as the only import
- define exactly one class named GeneratedScene
- do not use external files, network calls, shell commands, file I/O, exec, eval, or unsafe imports
- do not use Color(...), ManimColor(...), hsl=, rgb_to_color, colour, or manim.utils.color
- use only built-in color constants: WHITE, BLACK, BLUE, BLUE_E, GREEN, GREEN_E, RED, RED_E, YELLOW, ORANGE, PURPLE, GREY, GRAY
- keep all objects inside a 16:9 frame
- preserve the original creative metaphor and visual style whenever possible
- repair the smallest executable problem before simplifying the whole scene
- keep the scene reliable without turning it into generic Step 1 / Step 2 cards
- do not show generic visible labels like "Step 1", "Segment 1", or "Visual Step 1"
- if repairing visual_too_short_for_spoken_answer, extend timing/waits and implement every visual step instead of returning an intro-only scene
- keep total visual duration at or below 90 seconds
"""


def _combined_teaching_system_prompt() -> str:
    return "\n\n".join(
        [
            _COMBINED_TEACHING_SYSTEM_BASE,
            _manim_generation_style_prompt(),
            _manim_latex_prompt_rules(),
        ]
    )


def _clarify_roadmap_part_system_prompt() -> str:
    return "\n\n".join(
        [
            _CLARIFY_ROADMAP_PART_SYSTEM,
            _manim_generation_style_prompt(),
            _manim_latex_prompt_rules(),
        ]
    )


def _manim_repair_system_prompt(*, force_no_latex: bool = False, rejection_reason: str = "") -> str:
    return "\n\n".join(
        [
            _MANIM_REPAIR_SYSTEM,
            _manim_repair_style_prompt(),
            _manim_latex_prompt_rules(force_no_latex=force_no_latex, rejection_reason=rejection_reason),
        ]
    )


def build_roadmap_part_context(roadmap: dict[str, Any] | None, part: dict[str, Any] | None) -> str:
    if not part:
        return ""
    lines = [
        f"ROADMAP_TITLE: {(roadmap or {}).get('title') or ''}",
        f"ROADMAP_SUMMARY: {(roadmap or {}).get('summary') or ''}",
        f"ROADMAP_TOPICS: {', '.join((roadmap or {}).get('topics') or [])}",
        f"PART_TITLE: {part.get('title') or ''}",
        f"PART_SUMMARY: {part.get('summary') or ''}",
        f"START_TIME_SEC: {part.get('start_time') or 0}",
        f"END_TIME_SEC: {part.get('end_time') or 0}",
        f"CONCEPTS: {', '.join(part.get('concepts') or [])}",
        f"EQUATIONS: {', '.join(part.get('equations') or [])}",
        f"EXAMPLES: {', '.join(part.get('examples') or [])}",
        f"SUGGESTED_VISUALS: {', '.join(part.get('suggested_visuals') or [])}",
        "TRANSCRIPT_CHUNK_BEGIN",
        (part.get("transcript_chunk") or "")[:6000],
        "TRANSCRIPT_CHUNK_END",
    ]
    return "\n".join(lines)


def _build_part_context(part: dict[str, Any] | None) -> str:
    return build_roadmap_part_context(None, part)


def _build_user_prompt_video_context(*, persona_prompt: str, student_name: str, topic: str, part: dict[str, Any] | None, student_query: str, history_excerpt: str = "") -> str:
    return "\n".join(
        [
            "PERSONA_PROMPT_BEGIN",
            persona_prompt or "(no persona prompt yet)",
            "PERSONA_PROMPT_END",
            f"STUDENT_NAME: {student_name or 'Student'}",
            f"TOPIC: {topic}",
            "CURRENT_VIDEO_PART_BEGIN",
            _build_part_context(part),
            "CURRENT_VIDEO_PART_END",
            "RECENT_CONVERSATION_BEGIN",
            history_excerpt or "(none)",
            "RECENT_CONVERSATION_END",
            f"STUDENT_QUERY: {student_query}",
        ]
    )


def _build_user_prompt_persona_only(*, persona_prompt: str, student_name: str, topic: str, student_query: str, history_excerpt: str = "") -> str:
    return "\n".join(
        [
            "PERSONA_PROMPT_BEGIN",
            persona_prompt or "(no persona prompt yet)",
            "PERSONA_PROMPT_END",
            f"STUDENT_NAME: {student_name or 'Student'}",
            f"TOPIC: {topic}",
            "RECENT_CONVERSATION_BEGIN",
            history_excerpt or "(none)",
            "RECENT_CONVERSATION_END",
            f"STUDENT_QUERY: {student_query}",
        ]
    )


def _build_teach_roadmap_part_prompt(
    *,
    persona_prompt: str,
    student_name: str,
    teacher_name: str,
    teacher_profession: str,
    roadmap: dict[str, Any] | None,
    part: dict[str, Any] | None,
) -> str:
    return f"""
Teacher persona prompt:
{persona_prompt or "(no persona prompt yet)"}

Student name:
{student_name or "Student"}

Teacher name:
{teacher_name or "Teacher"}

Teacher profession:
{teacher_profession or "subject expert"}

Roadmap title:
{(roadmap or {}).get("title") or ""}

Current part title:
{(part or {}).get("title") or ""}

Current part context:
{build_roadmap_part_context(roadmap, part)}

Task:
Teach this current roadmap part in the teacher's style.

Rules:
- Use only this part as the main source.
- Keep it conversational and voice-friendly.
- Do not create a long textual board.
- Do not jump to later parts.
- Explain this part clearly.
- End by asking: "{PART_UNDERSTANDING_QUESTION}"

Return JSON:
{{
  "speechText": "string",
  "askFollowUp": "{PART_UNDERSTANDING_QUESTION}"
}}
""".strip()


def _build_clarify_roadmap_part_prompt(
    *,
    persona_prompt: str,
    student_name: str,
    teacher_name: str,
    topic: str,
    roadmap: dict[str, Any] | None,
    part: dict[str, Any] | None,
    student_query: str,
) -> str:
    return f"""
Teacher persona prompt:
{persona_prompt or "(no persona prompt yet)"}

Student name:
{student_name or "Student"}

Teacher name:
{teacher_name or "Teacher"}

Current topic:
{topic}

Roadmap title:
{(roadmap or {}).get("title") or ""}

Current part title:
{(part or {}).get("title") or ""}

Current part context:
{build_roadmap_part_context(roadmap, part)}

Student doubt:
{student_query}

Task:
Clarify the student's doubt using the current roadmap part.
Generate two coordinated outputs:
1. speech answer
2. Manim visual/code

Rules:
- Answer in the teacher's style.
- Stay grounded in the current part.
- Use Manim visual if it helps.
- Keep speech and visual explanation aligned.
- Ask whether the student understands now.

Return JSON:
{{
  "speech": {{
    "text": "string",
    "timestamps": [
      {{ "start": 0.0, "end": 3.2, "text": "..." }}
    ]
  }},
  "visual": {{
    "visualNeeded": true,
    "visualType": "manim",
    "manimCode": "string",
    "timestamps": [
      {{ "start": 0.0, "end": 3.2, "cue": "..." }}
    ]
  }},
  "askFollowUp": "{CLARIFICATION_FOLLOWUP}"
}}
""".strip()


def _build_combined_teaching_user_prompt(
    *,
    mode: str,
    persona_prompt: str,
    teacher_name: str,
    teacher_profession: str,
    student_name: str,
    topic: str,
    student_query: str,
    part_context: str,
    session_memory: dict[str, Any] | None = None,
    previous_assistant_answer: str = "",
) -> str:
    memory = session_memory if isinstance(session_memory, dict) else {}
    memory_json = json.dumps(memory, ensure_ascii=False, indent=2) if memory else "{}"
    return f"""
Mode:
{mode}

Teacher persona prompt:
{persona_prompt or "(no persona prompt yet)"}

Teacher:
{teacher_name or "Teacher"}, {teacher_profession or "subject expert"}

Student:
{student_name or "Student"}

Topic:
{topic}

Student question/doubt:
{student_query}

SESSION_MEMORY_JSON:
{memory_json}

You previously gave this answer to the student:
{previous_assistant_answer or "(none)"}

The student has now replied:
{student_query}

Continue from the previous answer. Do not restart. Do not repeat the same explanation unless the student asks to repeat. Advance the teaching one step at a time.

Current roadmap/video part context:
{part_context or "(none)"}

Task:
Generate a synchronized teaching response using the required JSON shape.

Rules:
- Speech should sound like the teacher is speaking naturally.
- Use SESSION_MEMORY_JSON to continue the current step and next teaching goal.
- Use the previous answer and current reply block above as the continuity anchor.
- visual_plan_with_timestamps must align with the spoken answer.
- spoken_segments and visual_steps must cover the full answer from beginning to end.
- Estimate the spoken answer duration at 140 words per minute, then set estimated_total_visual_duration_seconds.
- The Manim code must implement every visual_step in order and target the estimated spoken duration, capped at 90 seconds.
- Do not make a 20 second animation for a 60-80 second spoken answer.
- Visual should be Manim-based.
- Avoid static board-like visuals.
- Prefer animation: moving arrows, highlighting, progressive reveal, transformations, step-by-step diagrams.
- If formulas are needed, use simple text unless LaTeX is available.
- Keep visual latency reasonable.
- Keep Manim code simple enough to render quickly.
- End with a follow-up question.

Return JSON only in the required shape from the system prompt.
""".strip()


def _fallback_combined_manim_code(
    *,
    title: str,
    topic: str,
    speech_segments: list[dict[str, Any]],
    visual_segments: list[dict[str, Any]],
    target_duration_seconds: float | None = None,
) -> str:
    title_literal = json.dumps(_trim_sentence(title or topic or "Visual explanation", 46))
    topic_literal = json.dumps(_trim_sentence(topic or "core idea", 54))
    steps = []
    count = max(len(speech_segments), len(visual_segments), 1)
    total_target = target_duration_seconds or sum(_safe_float(item.get("estimated_duration_seconds"), 0.0) for item in speech_segments) or 12.0
    total_target = _target_visual_duration_seconds(total_target)
    base_durations = []
    for index in range(count):
        speech = speech_segments[min(index, len(speech_segments) - 1)] if speech_segments else {}
        visual = visual_segments[min(index, len(visual_segments) - 1)] if visual_segments else {}
        label = _trim_sentence(
            visual.get("description") or visual.get("text") or visual.get("cue") or speech.get("text") or "Key idea",
            54,
        )
        speech_text = _trim_sentence(speech.get("text") or label, 86)
        duration = _safe_float(visual.get("duration_seconds") or speech.get("estimated_duration_seconds"), estimate_spoken_duration_seconds(speech_text) or 4.0)
        base_durations.append(max(2.4, duration))
        steps.append({"label": label, "speech": speech_text, "duration": duration})
    durations = _scale_durations_to_target(base_durations, total_target)
    for index, duration in enumerate(durations):
        steps[index]["duration"] = duration
    steps_literal = json.dumps(steps)
    return f'''from manim import *

{_MANIM_REGION_HELPERS_CODE}

class {MANIM_SCENE_CLASS_NAME}(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        active_regions = {{"title": None, "left": None, "right": None, "bottom": None}}
        title = safe_text({title_literal}, font_size=34, max_width=12.0)
        place_title(title)
        replace_region(self, active_regions, "title", title)

        steps = {steps_literal}
        topic_label = {topic_literal}
        for index, step in enumerate(steps):
            label = step.get("label") or "Key idea"
            speech = step.get("speech") or label
            duration = max(2.4, float(step.get("duration") or 4.0))
            focus = safe_text(label, font_size=22, max_width=11.5)
            place_bottom(focus)
            replace_region(self, active_regions, "bottom", focus)

            left_items = [label, speech]
            if index == 0:
                left_items.append(topic_label)
            left_panel = bullet_list(left_items[:3], max_width=5.25, font_size=24)
            place_left(left_panel)
            replace_region(self, active_regions, "left", left_panel)

            dot = Circle(radius=0.38, color=BLUE)
            bridge = Rectangle(width=1.18, height=0.68, color=YELLOW if index % 2 else GREEN)
            result = Circle(radius=0.38, color=ORANGE)
            flow = VGroup(dot, bridge, result).arrange(RIGHT, buff=0.72)
            arrows = VGroup(
                Arrow(dot.get_right(), bridge.get_left(), buff=0.12, color=BLUE),
                Arrow(bridge.get_right(), result.get_left(), buff=0.12, color=BLUE),
            )
            diagram_title = safe_text(label, font_size=24, max_width=5.1)
            diagram = VGroup(diagram_title, VGroup(flow, arrows)).arrange(DOWN, buff=0.35)
            place_right(diagram)
            replace_region(self, active_regions, "right", diagram)
            self.wait(max(0.4, duration - 2.0))

        takeaway = safe_text("Pause here: connect each step back to the main idea.", font_size=24, max_width=11.5)
        place_bottom(takeaway)
        replace_region(self, active_regions, "bottom", takeaway)
        self.wait(0.8)
'''


def _fallback_combined_response(
    *,
    mode: str,
    topic: str,
    student_query: str,
    part: dict[str, Any] | None,
    teacher_name: str,
) -> dict[str, Any]:
    title = (part or {}).get("title") or topic or "this idea"
    if mode == "persona_only_teaching":
        spoken_parts = [
            f"I have not uploaded a video for {topic or 'that topic'} yet, but I can still explain it in {teacher_name or 'this teacher'}'s style.",
            f"Start with the simplest version: {student_query or topic or 'the idea'} has a few parts that work together.",
            "Watch the visual move from the first part to the result so the relationship is clear.",
            "Now use that same relationship when you see a new example.",
        ]
    else:
        summary = (part or {}).get("summary") or (part or {}).get("transcript_chunk") or topic
        spoken_parts = [
            f"Let's slow down on {title}.",
            f"The video part is pointing to this idea: {_trim_sentence(summary, 140)}",
            f"Your doubt is about {student_query or 'this step'}, so watch how the pieces connect.",
            "The key is to follow the cause, then the change, then the result.",
        ]
    cursor = 0.0
    speech_segments = []
    for index, text in enumerate(spoken_parts, start=1):
        duration = max(3.0, estimate_spoken_duration_seconds(text))
        speech_segments.append(
            {
                "id": f"seg_{index}",
                "start": round(cursor, 1),
                "end": round(cursor + duration, 1),
                "text": text,
                "estimated_duration_seconds": round(duration, 2),
                "matching_visual_step_id": f"vis_{index}",
            }
        )
        cursor += duration
    visual_segments = [
        {
            "id": f"vis_{index}",
            "start": item["start"],
            "end": item["end"],
            "duration_seconds": round(item["end"] - item["start"], 2),
            "matching_spoken_segment_id": item["id"],
            "description": desc,
            "region": "left" if index % 2 else "right",
        }
        for index, (item, desc) in enumerate(
            zip(
                speech_segments,
                ["Reveal the first idea", "Move the marker to the changing step", "Highlight the result", "Circle the whole pattern"],
            ),
            start=1,
        )
    ]
    speech_text = " ".join(item["text"] for item in speech_segments)
    estimated_spoken = estimate_spoken_duration_seconds(speech_text)
    target_visual = _target_visual_duration_seconds(estimated_spoken)
    return {
        "speech": {"text": speech_text, "segments": speech_segments, "timestamps": speech_segments, "estimated_duration_seconds": estimated_spoken},
        "visual": {
            "visualNeeded": True,
            "visualType": "manim",
            "style": "interactive_teacher_visual",
            "segments": visual_segments,
            "visual_steps": visual_segments,
            "timestamps": [{"start": item["start"], "end": item["end"], "cue": item["description"]} for item in visual_segments],
            "manimCode": _fallback_combined_manim_code(
                title=title,
                topic=topic,
                speech_segments=speech_segments,
                visual_segments=visual_segments,
                target_duration_seconds=target_visual,
            ),
            "estimatedTotalVisualDurationSeconds": target_visual,
        },
        "syncPlan": {
            "segments": [
                {
                    "speechText": item["text"],
                    "visualCue": visual_segments[index]["description"] if index < len(visual_segments) else "",
                    "startHint": item["start"],
                }
                for index, item in enumerate(speech_segments)
            ]
        },
        "teachingControl": {"askFollowUp": CLARIFICATION_FOLLOWUP, "nextAction": "await_student_response"},
        "askFollowUp": CLARIFICATION_FOLLOWUP,
        "spoken_segments": speech_segments,
        "visual_steps": visual_segments,
        "estimated_spoken_duration_seconds": estimated_spoken,
        "estimated_total_visual_duration_seconds": target_visual,
        "debug": {"source": "local_combined_fallback", "mode": mode},
    }


def _normalize_combined_speech(raw_speech: Any, *, fallback_text: str) -> dict[str, Any]:
    speech = raw_speech if isinstance(raw_speech, dict) else {}
    text = _clean_spaces(speech.get("text") or fallback_text)
    raw_segments = speech.get("spoken_segments") if isinstance(speech.get("spoken_segments"), list) else speech.get("spokenSegments")
    if not isinstance(raw_segments, list):
        raw_segments = speech.get("segments") if isinstance(speech.get("segments"), list) else speech.get("timestamps")
    segments = _normalize_spoken_segments(raw_segments, text or fallback_text)
    if not text:
        text = " ".join(item["text"] for item in segments)
    return {
        "text": text,
        "segments": segments,
        "timestamps": segments,
        "word_count": _word_count(text),
        "estimated_duration_seconds": estimate_spoken_duration_seconds(text),
    }


def _normalize_visual_plan_item(raw_item: dict[str, Any], *, index: int, speech: dict[str, Any]) -> dict[str, Any]:
    desc = _clean_spaces(raw_item.get("description") or raw_item.get("cue") or raw_item.get("visualCue"))
    if not desc:
        desc = _clean_spaces(raw_item.get("matches_spoken_text") or raw_item.get("matchesSpeechSegmentId"))
    if not desc:
        desc = f"Animate the idea in speech segment {index}"
    return {
        "id": _clean_spaces(raw_item.get("id")) or f"vis_{index}",
        "start": _safe_float(raw_item.get("start"), speech.get("start", 0.0)),
        "end": _safe_float(raw_item.get("end"), speech.get("end", 0.0)),
        "matchesSpeechSegmentId": _clean_spaces(raw_item.get("matchesSpeechSegmentId") or raw_item.get("matches_spoken_text")) or speech.get("id") or f"seg_{index}",
        "description": desc,
    }


def _coerce_unified_teaching_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    if not any(key in parsed for key in ("spoken_answer", "spoken_segments", "visual_steps", "teaching_state_update", "visual_plan_with_timestamps", "manim_code", "follow_up_question")):
        return parsed
    spoken_answer = _clean_spaces(parsed.get("spoken_answer") or parsed.get("spokenAnswer"))
    raw_spoken_segments = parsed.get("spoken_segments") if isinstance(parsed.get("spoken_segments"), list) else parsed.get("spokenSegments")
    speech_segments = _normalize_spoken_segments(raw_spoken_segments, spoken_answer)
    visual_plan = parsed.get("visual_steps") if isinstance(parsed.get("visual_steps"), list) else parsed.get("visualSteps")
    if not isinstance(visual_plan, list):
        visual_plan = parsed.get("visual_plan_with_timestamps")
    if not isinstance(visual_plan, list):
        visual_plan = parsed.get("visualPlanWithTimestamps") if isinstance(parsed.get("visualPlanWithTimestamps"), list) else []
    visual_segments = _normalize_visual_steps(visual_plan, speech_segments)
    estimated_spoken = estimate_spoken_duration_seconds(spoken_answer)
    target_visual = _target_visual_duration_seconds(estimated_spoken)
    return {
        "speech": {
            "text": spoken_answer,
            "segments": speech_segments,
            "spoken_segments": speech_segments,
            "word_count": _word_count(spoken_answer),
            "estimated_duration_seconds": estimated_spoken,
        },
        "visual": {
            "visualNeeded": True,
            "visualType": "manim",
            "segments": visual_segments,
            "visual_steps": visual_segments,
            "manimCode": str(parsed.get("manim_code") or parsed.get("manimCode") or "").strip(),
            "estimatedTotalVisualDurationSeconds": _safe_float(parsed.get("estimated_total_visual_duration_seconds") or parsed.get("estimatedTotalVisualDurationSeconds"), target_visual),
        },
        "teachingControl": {
            "askFollowUp": _clean_spaces(parsed.get("follow_up_question") or parsed.get("followUpQuestion") or CLARIFICATION_FOLLOWUP),
            "nextAction": _clean_spaces((parsed.get("teaching_state_update") or {}).get("next_action") if isinstance(parsed.get("teaching_state_update"), dict) else "") or "await_student_response",
        },
        "teachingStateUpdate": parsed.get("teaching_state_update") if isinstance(parsed.get("teaching_state_update"), dict) else {},
        "debug": {
            "raw_schema": "unified_structured_v2",
            "spoken_segments": len(speech_segments),
            "visual_steps": len(visual_segments),
            "estimated_spoken_duration_seconds": estimated_spoken,
            "estimated_total_visual_duration_seconds": target_visual,
        },
    }


def _normalize_combined_visual(raw_visual: Any, *, speech_segments: list[dict[str, Any]], fallback_code: str) -> dict[str, Any]:
    visual = raw_visual if isinstance(raw_visual, dict) else {}
    raw_segments = visual.get("visual_steps") if isinstance(visual.get("visual_steps"), list) else visual.get("visualSteps")
    if not isinstance(raw_segments, list):
        raw_segments = visual.get("segments") if isinstance(visual.get("segments"), list) else visual.get("timestamps")
    segments = _normalize_visual_steps(raw_segments, speech_segments)
    code = str(visual.get("manimCode") or visual.get("code") or "").strip()
    code_source = "ai_generated" if code else "local_fallback"
    validation_error = direct_manim_validation_error(code) if code else "empty Manim code"
    if not code:
        code = fallback_code
        validation_error = direct_manim_validation_error(code)
    elif validation_error:
        logger.warning("[teaching-pipeline] generated manimCode failed validation; repair/fallback will run before render error=%s", validation_error)
    return {
        "visualNeeded": bool(visual.get("visualNeeded", True)),
        "visualType": "manim",
        "style": _clean_spaces(visual.get("style")) or "interactive_teacher_visual",
        "segments": segments,
        "visual_steps": segments,
        "timestamps": [{"start": item["start"], "end": item["end"], "cue": item["description"]} for item in segments],
        "manimCode": code,
        "manimPlan": visual.get("manimPlan") or "",
        "manimCodeSource": code_source,
        "manimCodeValidationError": validation_error,
        "estimatedTotalVisualDurationSeconds": round(sum(item.get("duration_seconds", 0.0) for item in segments), 2),
    }


def _normalize_combined_teaching_response(
    raw: dict[str, Any],
    *,
    mode: str,
    topic: str,
    student_query: str,
    part: dict[str, Any] | None,
    teacher_name: str,
) -> dict[str, Any]:
    fallback = _fallback_combined_response(mode=mode, topic=topic, student_query=student_query, part=part, teacher_name=teacher_name)
    parsed = _coerce_unified_teaching_payload(raw) if isinstance(raw, dict) and raw else fallback
    parsed = parsed if isinstance(parsed, dict) and parsed else fallback
    fallback_speech = (fallback.get("speech") or {}).get("text") or ""
    speech = _normalize_combined_speech(parsed.get("speech"), fallback_text=fallback_speech)
    title = (part or {}).get("title") or topic or "Visual explanation"
    fallback_code = _fallback_combined_manim_code(
        title=title,
        topic=topic,
        speech_segments=speech["segments"],
        visual_segments=(fallback.get("visual") or {}).get("segments") or [],
        target_duration_seconds=_target_visual_duration_seconds(speech.get("estimated_duration_seconds") or estimate_spoken_duration_seconds(speech["text"])),
    )
    visual = _normalize_combined_visual(parsed.get("visual"), speech_segments=speech["segments"], fallback_code=fallback_code)
    control = parsed.get("teachingControl") if isinstance(parsed.get("teachingControl"), dict) else {}
    ask_follow_up = _clean_spaces(control.get("askFollowUp") or parsed.get("askFollowUp") or CLARIFICATION_FOLLOWUP)
    next_action = _clean_spaces(control.get("nextAction") or "await_student_response")
    if next_action not in {"await_student_response", "continue_next_part", "complete"}:
        next_action = "await_student_response"
    state_update = parsed.get("teachingStateUpdate") or parsed.get("teaching_state_update")
    if not isinstance(state_update, dict):
        state_update = {}
    state_update = {
        "current_topic": _clean_spaces(state_update.get("current_topic") or topic),
        "current_step": _clean_spaces(state_update.get("current_step") or (part or {}).get("title") or ""),
        "last_assistant_answer": speech["text"],
        "last_visual_plan": visual.get("segments") or [],
        "student_understanding_summary": _clean_spaces(state_update.get("student_understanding_summary") or ""),
        "unresolved_student_question": _clean_spaces(state_update.get("unresolved_student_question") or student_query),
        "next_teaching_goal": _clean_spaces(state_update.get("next_teaching_goal") or ask_follow_up),
    }
    raw_debug = parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}
    estimated_spoken = estimate_spoken_duration_seconds(speech["text"])
    estimated_visual = round(sum(item.get("duration_seconds", 0.0) for item in visual.get("segments") or []), 2)
    return {
        "speech": speech,
        "visual": visual,
        "teachingStateUpdate": state_update,
        "visualPlanWithTimestamps": visual.get("segments") or [],
        "spoken_segments": speech.get("segments") or [],
        "visual_steps": visual.get("segments") or [],
        "estimated_spoken_duration_seconds": estimated_spoken,
        "estimated_total_visual_duration_seconds": estimated_visual or _target_visual_duration_seconds(estimated_spoken),
        "spoken_answer": speech["text"],
        "manim_code": visual.get("manimCode") or "",
        "follow_up_question": ask_follow_up,
        "syncPlan": {
            "segments": [
                {
                    "speechText": speech_item["text"],
                    "visualCue": (visual["segments"][index]["description"] if index < len(visual["segments"]) else ""),
                    "startHint": speech_item["start"],
                }
                for index, speech_item in enumerate(speech["segments"])
            ]
        },
        "teachingControl": {"askFollowUp": ask_follow_up, "nextAction": next_action},
        "askFollowUp": ask_follow_up,
        "debug": {
            **raw_debug,
            "source": "combined_teaching_pipeline",
            "mode": mode,
            "word_count": _word_count(speech["text"]),
            "estimated_spoken_duration_seconds": estimated_spoken,
            "estimated_total_visual_duration_seconds": estimated_visual or _target_visual_duration_seconds(estimated_spoken),
            "speech_segment_count": len(speech.get("segments") or []),
            "visual_step_count": len(visual.get("segments") or []),
        },
    }


async def generate_teaching_response_with_visuals(
    *,
    mode: str,
    persona_prompt: str,
    teacher_name: str,
    teacher_profession: str,
    student_name: str,
    topic: str,
    student_query: str,
    current_roadmap_part: dict[str, Any] | None = None,
    part_context: str = "",
    available_visual_mode: str = "manim",
    session_memory: dict[str, Any] | None = None,
    previous_assistant_answer: str = "",
) -> dict[str, Any]:
    del available_visual_mode
    normalized_mode = mode if mode in {"video_context_clarification", "persona_only_teaching"} else "persona_only_teaching"
    cfg = get_model_config("teaching_pipeline")
    logger.info("[teaching-pipeline] provider=%s model=%s mode=%s manim_visual_style=%s", cfg.provider, cfg.model, normalized_mode, _manim_visual_style())
    logger.info(
        "[student-models] teaching_pipeline provider=%s model=%s visual_generation_provider=%s visual_generation_model=%s",
        cfg.provider,
        cfg.model,
        cfg.provider,
        cfg.model,
    )
    logger.info("[visual-routing] primary=manim board=false")
    logger.info("[visual-routing] board pipeline skipped because manim is primary")
    full_part_context = part_context or build_roadmap_part_context(None, current_roadmap_part)
    user_prompt = _build_combined_teaching_user_prompt(
        mode=normalized_mode,
        persona_prompt=persona_prompt,
        teacher_name=teacher_name,
        teacher_profession=teacher_profession,
        student_name=student_name,
        topic=topic,
        student_query=student_query,
        part_context=full_part_context,
        session_memory=session_memory,
        previous_assistant_answer=previous_assistant_answer,
    )
    memory = session_memory if isinstance(session_memory, dict) else {}
    recent_turns = memory.get("recent_turns") if isinstance(memory.get("recent_turns"), list) else []
    logger.info(
        "[teaching-pipeline] context session_memory=%s recent_turns=%s previous_assistant_included=%s current_topic=%s current_step=%s next_goal=%s",
        bool(memory),
        len(recent_turns),
        bool(previous_assistant_answer),
        memory.get("current_topic") or topic,
        memory.get("current_step") or "",
        memory.get("next_teaching_goal") or "",
    )
    raw: dict[str, Any] = {}
    try:
        raw = await llm_json(
            "teaching_pipeline",
            _combined_teaching_system_prompt(),
            user_prompt,
            max_tokens=6000,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[teaching-pipeline] combined generation failed; using local fallback mode=%s error=%s", normalized_mode, exc)
    normalized = _normalize_combined_teaching_response(
        raw,
        mode=normalized_mode,
        topic=topic,
        student_query=student_query,
        part=current_roadmap_part,
        teacher_name=teacher_name,
    )
    debug = normalized.get("debug") if isinstance(normalized.get("debug"), dict) else {}
    debug.update(
        {
            "model_provider": cfg.provider,
            "model": cfg.model,
            "model_source": cfg.source,
            "structured_response_schema": "spoken_segments_visual_steps_manim_code_v2",
            "openai_used": bool(cfg.provider == "openai" and raw),
            "llm_response_received": bool(raw),
            "manim_code_source": (normalized.get("visual") or {}).get("manimCodeSource") or "unknown",
            "manim_visual_style": _manim_visual_style(),
            "previous_assistant_answer_included": bool(previous_assistant_answer),
            "recent_turns_included": len(recent_turns),
        }
    )
    normalized["debug"] = debug
    logger.info(
        "[av-sync] word_count=%s estimated_spoken_duration=%s visual_steps=%s spoken_segments=%s result=planned",
        debug.get("word_count"),
        debug.get("estimated_spoken_duration_seconds"),
        debug.get("visual_step_count"),
        debug.get("speech_segment_count"),
    )
    logger.info(
        "[teaching-pipeline] response has speech=%s visual=%s manimCode=%s manim_source=%s openai_used=%s",
        bool((normalized.get("speech") or {}).get("text")),
        bool(normalized.get("visual")),
        bool((normalized.get("visual") or {}).get("manimCode")),
        debug.get("manim_code_source"),
        debug.get("openai_used"),
    )
    return normalized


def build_fallback_manim_code(
    *,
    title: str,
    topic: str,
    spoken_answer: str,
    visual_plan: list[dict[str, Any]] | None = None,
    target_duration_seconds: float | None = None,
) -> str:
    speech = _normalize_combined_speech({"text": spoken_answer or title or topic}, fallback_text=spoken_answer or title or topic or "Visual explanation")
    visual_segments = []
    for index, item in enumerate(visual_plan or [], start=1):
        if not isinstance(item, dict):
            continue
        visual_segments.append(
            {
                "id": _clean_spaces(item.get("id")) or f"vis_{index}",
                "start": _safe_float(item.get("start"), 0.0),
                "end": _safe_float(item.get("end"), 3.0),
                "description": _clean_spaces(item.get("description") or item.get("cue") or item.get("visualCue") or item.get("matches_spoken_text")),
            }
        )
    return _fallback_combined_manim_code(
        title=title or topic or "Visual explanation",
        topic=topic or title or "core idea",
        speech_segments=speech.get("segments") or [],
        visual_segments=visual_segments,
        target_duration_seconds=target_duration_seconds or _target_visual_duration_seconds(estimate_spoken_duration_seconds(spoken_answer)),
    )


async def repair_manim_code_with_error(
    *,
    failed_code: str,
    error_log: str,
    spoken_answer: str,
    visual_plan: list[dict[str, Any]] | None = None,
    spoken_segments: list[dict[str, Any]] | None = None,
    visual_steps: list[dict[str, Any]] | None = None,
    estimated_spoken_duration_seconds: float | None = None,
    actual_manim_duration_seconds: float | None = None,
    topic: str = "",
    title: str = "",
) -> dict[str, Any]:
    cfg = get_model_config("visual")
    style = _manim_visual_style()
    logger.info("[student-models] repair_generation provider=%s model=%s", cfg.provider, cfg.model)
    logger.info("[manim] repair request provider=%s model=%s error_chars=%s failed_code_chars=%s", cfg.provider, cfg.model, len(error_log or ""), len(failed_code or ""))
    user = f"""
Topic:
{topic}

Title:
{title}

Spoken answer the visual must support:
{spoken_answer}

Spoken segments:
{json.dumps(spoken_segments or [], ensure_ascii=False)}

Visual plan with timestamps:
{json.dumps(visual_plan or [], ensure_ascii=False)}

Visual steps:
{json.dumps(visual_steps or visual_plan or [], ensure_ascii=False)}

Estimated spoken duration seconds:
{estimated_spoken_duration_seconds if estimated_spoken_duration_seconds is not None else estimate_spoken_duration_seconds(spoken_answer)}

Actual rendered Manim duration seconds, if already rendered:
{actual_manim_duration_seconds if actual_manim_duration_seconds is not None else "not_rendered_yet"}

Failed Manim code:
{failed_code}

Full validation/render error log, including final stderr/stdout tails when available:
{error_log}

Task:
Repair the Manim code so it renders safely and still follows the same spoken answer and visual plan.
Active Manim visual style:
{style}

If the error is visual_too_short_for_spoken_answer, rewrite or extend the scene so it covers every spoken segment and targets 75-100% of the estimated spoken duration, capped at 90 seconds.
If the previous video was only about 20 seconds for a much longer explanation, do not merely patch syntax; rewrite the timing and waits to cover all spoken segments.
Simplify the scene if the error came from animation complexity, mobject state, transform matching, or unsupported Manim behavior.
Preserve the educational idea, creative metaphor, object motion, and animation sequence.
Do not replace the scene with generic Step 1 / Step 2 cards.
Do not show generic visible text such as "Step 1", "Segment 1", or "Visual Step 1"; use meaningful concept labels.
If strict_layout mode is active, rewrite using the region-based layout helpers. In creative_safe mode, use helpers only when they are the cleanest fix.
Before showing new content in an occupied area, FadeOut or Transform the old group first.
Avoid complex transforms if they caused failure; prefer simple FadeOut/FadeIn region replacement.
Use only built-in Manim primitives and avoid external assets.
""".strip()
    try:
        result = await llm_json(
            "visual",
            _manim_repair_system_prompt(force_no_latex=manim_text_only_mode(), rejection_reason=error_log[:400]),
            user,
            max_tokens=4500,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[manim] repair call failed provider=%s model=%s error=%s", cfg.provider, cfg.model, exc)
        return {"manim_code": "", "source": "repair_failed", "error": str(exc), "model_provider": cfg.provider, "model": cfg.model}
    code = str(result.get("manim_code") or result.get("manimCode") or result.get("code") or "").strip() if isinstance(result, dict) else ""
    validation_error = direct_manim_validation_error(code) if code else "empty repaired Manim code"
    if validation_error:
        logger.warning("[manim] repaired code failed validation error=%s", validation_error)
        return {
            "manim_code": code,
            "source": "repair_invalid",
            "error": validation_error,
            "model_provider": cfg.provider,
            "model": cfg.model,
        }
    logger.info("[manim] repaired code passed validation chars=%s", len(code))
    return {
        "manim_code": code,
        "source": "ai_repaired",
        "error": None,
        "model_provider": cfg.provider,
        "model": cfg.model,
    }


def _stub_answer(*, mode: str, topic: str, part: dict[str, Any] | None, student_query: str) -> dict[str, Any]:
    """Deterministic placeholder that respects the schema; used when no LLM
    is configured (or as a fallback when the LLM call fails).
    """
    if mode == "video_context":
        first_concept = ((part or {}).get("concepts") or [None])[0]
        body = (
            f"Let's pick up where this video covers {topic}. "
            f"In this part we focus on {(part or {}).get('title') or topic}. "
            f"The key idea is {first_concept or topic} — let me walk through it slowly. "
            f"You asked: {student_query.strip()}. Here's how I'd think about it step by step."
        )
        return {
            "spokenAnswer": body,
            "shortSummary": f"{topic}: {first_concept or 'core idea'}.",
            "visualNeeded": True,
            "visualType": "manim" if part and (part.get("equations") or part.get("suggested_visuals")) else "none",
            "visualPrompt": (part.get("suggested_visuals") or [topic])[0] if part else topic,
            "askFollowUp": "Does that make sense, or should I slow down on any step?",
        }
    return {
        "spokenAnswer": (
            f"That topic isn't in the uploaded videos yet, but I'll explain it in my style. "
            f"You asked: {student_query.strip()}. Here's how I'd approach {topic} — let's "
            f"start from the simplest version and build up."
        ),
        "disclaimer": "This topic isn't from the uploaded videos, but I can explain it in this teacher's style.",
        "visualNeeded": True,
        "visualType": "manim",
        "visualPrompt": topic,
        "askFollowUp": "Want me to keep going, or pause for a question?",
    }


async def answer_video_context(
    *,
    persona_prompt: str,
    student_name: str,
    topic: str,
    part: dict[str, Any] | None,
    student_query: str,
    history_excerpt: str = "",
) -> dict[str, Any]:
    user = _build_user_prompt_video_context(
        persona_prompt=persona_prompt,
        student_name=student_name,
        topic=topic,
        part=part,
        student_query=student_query,
        history_excerpt=history_excerpt,
    )
    payload = await llm_json("answer", _VIDEO_CONTEXT_SYSTEM, user, max_tokens=1500)
    return _normalize_video_context(payload, topic=topic, part=part, student_query=student_query)


async def answer_persona_only(
    *,
    persona_prompt: str,
    student_name: str,
    topic: str,
    student_query: str,
    history_excerpt: str = "",
) -> dict[str, Any]:
    user = _build_user_prompt_persona_only(
        persona_prompt=persona_prompt,
        student_name=student_name,
        topic=topic,
        student_query=student_query,
        history_excerpt=history_excerpt,
    )
    payload = await llm_json("answer", _PERSONA_ONLY_SYSTEM, user, max_tokens=1500)
    return _normalize_persona_only(payload, topic=topic, student_query=student_query)


async def teach_roadmap_part(
    *,
    persona_prompt: str,
    student_name: str,
    teacher_name: str,
    teacher_profession: str,
    roadmap: dict[str, Any] | None,
    part: dict[str, Any] | None,
) -> dict[str, Any]:
    part_id = (part or {}).get("id") or "-"
    logger.info("teaching roadmap part prompt sent roadmap=%s part=%s", (roadmap or {}).get("id") or "-", part_id)
    user = _build_teach_roadmap_part_prompt(
        persona_prompt=persona_prompt,
        student_name=student_name,
        teacher_name=teacher_name,
        teacher_profession=teacher_profession,
        roadmap=roadmap,
        part=part,
    )
    payload = await llm_json("answer", _TEACH_ROADMAP_PART_SYSTEM, user, max_tokens=1800, temperature=0.25)
    normalized = _normalize_teach_roadmap_part(payload, roadmap=roadmap, part=part)
    logger.info("teaching response received part=%s chars=%s", part_id, len(normalized.get("speechText") or ""))
    return normalized


async def clarify_roadmap_part(
    *,
    persona_prompt: str,
    student_name: str,
    teacher_name: str,
    topic: str,
    roadmap: dict[str, Any] | None,
    part: dict[str, Any] | None,
    student_query: str,
) -> dict[str, Any]:
    part_id = (part or {}).get("id") or "-"
    logger.info("clarification prompt sent roadmap=%s part=%s doubt_chars=%s", (roadmap or {}).get("id") or "-", part_id, len(student_query or ""))
    user = _build_clarify_roadmap_part_prompt(
        persona_prompt=persona_prompt,
        student_name=student_name,
        teacher_name=teacher_name,
        topic=topic,
        roadmap=roadmap,
        part=part,
        student_query=student_query,
    )
    payload = await llm_json(
        "clarification",
        _clarify_roadmap_part_system_prompt(),
        user,
        max_tokens=5600,
        temperature=0.25,
    )
    normalized = _normalize_clarify_roadmap_part(payload, topic=topic, part=part, student_query=student_query)
    normalized = await _ensure_visual_manim_code(
        normalized,
        persona_prompt=persona_prompt,
        student_name=student_name,
        teacher_name=teacher_name,
        topic=topic,
        roadmap=roadmap,
        part=part,
        student_query=student_query,
    )
    logger.info(
        "clarification response received part=%s speech_chars=%s speech_timestamps=%s visual_needed=%s manim_code=%s manim_timestamps=%s",
        part_id,
        len(((normalized.get("speech") or {}).get("text") or "")),
        len(((normalized.get("speech") or {}).get("timestamps") or [])),
        (normalized.get("visual") or {}).get("visualNeeded"),
        bool((normalized.get("visual") or {}).get("manimCode")),
        len(((normalized.get("visual") or {}).get("timestamps") or [])),
    )
    return normalized


async def _ensure_visual_manim_code(
    payload: dict[str, Any],
    *,
    persona_prompt: str,
    student_name: str,
    teacher_name: str,
    topic: str,
    roadmap: dict[str, Any] | None,
    part: dict[str, Any] | None,
    student_query: str,
) -> dict[str, Any]:
    visual = payload.get("visual") if isinstance(payload.get("visual"), dict) else {}
    if not visual.get("visualNeeded", True):
        return payload
    speech_text = ((payload.get("speech") or {}).get("text") or "").strip()
    cues = visual.get("timestamps") or (payload.get("syncPlan") or {}).get("segments") or []
    user = f"""
Teacher persona prompt:
{persona_prompt or "(no persona prompt yet)"}

Student name:
{student_name or "Student"}

Teacher name:
{teacher_name or "Teacher"}

Current topic:
{topic}

Roadmap title:
{(roadmap or {}).get("title") or ""}

Current part title:
{(part or {}).get("title") or ""}

Current roadmap part context:
{build_roadmap_part_context(roadmap, part)}

Student doubt:
{student_query}

Speech explanation:
{speech_text}

Visual cues/timestamps:
{json.dumps(cues, ensure_ascii=False)}
""".strip()
    text_only = manim_text_only_mode()
    try:
        result = await llm_json("visual", _manim_visual_system(), user, max_tokens=4200, temperature=0.15)
        code = (result.get("manimCode") or result.get("code") or "").strip() if isinstance(result, dict) else ""
        logger.info(
            "[manim] code_received length=%s text_only_mode=%s latex_available=%s manim_visual_style=%s",
            len(code),
            text_only,
            has_latex_available(),
            _manim_visual_style(),
        )
        validation_error = direct_manim_validation_error(code) if code else "empty Manim code"
        if code and validation_error:
            logger.warning("[manim] generated code failed validation error=%s; retrying stricter generation", validation_error)
            retry_user = (
                user
                + "\n\nRenderer rejected the previous Manim code before render. "
                f"Validation error: {validation_error}. "
                "Regenerate the complete file with simpler primitives and obey every renderer rule."
            )
            retry = await llm_json(
                "visual",
                _manim_visual_system(force_no_latex=text_only, rejection_reason=validation_error),
                retry_user,
                max_tokens=4200,
                temperature=0.1,
            )
            retry_code = (retry.get("manimCode") or retry.get("code") or "").strip() if isinstance(retry, dict) else ""
            if retry_code:
                code = retry_code
                retry_validation_error = direct_manim_validation_error(code)
                if retry_validation_error:
                    logger.warning("[manim] stricter regeneration still failed validation error=%s; renderer fallback will be used", retry_validation_error)
        if code:
            visual["manimCode"] = code
            payload["visual"] = visual
            logger.info("[manim] code_validation prepared chars=%s", len(code))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[manim] code generation failed; keeping clarification/fallback code: %s", exc)
    return payload


def _normalize_video_context(payload: dict[str, Any], *, topic: str, part: dict[str, Any] | None, student_query: str) -> dict[str, Any]:
    spoken = (payload.get("spokenAnswer") or "").strip()
    if not spoken:
        return _stub_answer(mode="video_context", topic=topic, part=part, student_query=student_query)
    return {
        "spokenAnswer": spoken,
        "shortSummary": (payload.get("shortSummary") or "").strip(),
        "visualNeeded": bool(payload.get("visualNeeded")),
        "visualType": _normalize_visual_type(payload.get("visualType")),
        "visualPrompt": (payload.get("visualPrompt") or "").strip(),
        "askFollowUp": (payload.get("askFollowUp") or "Does that make sense, or should I slow down?").strip(),
    }


def _normalize_teach_roadmap_part(payload: dict[str, Any], *, roadmap: dict[str, Any] | None, part: dict[str, Any] | None) -> dict[str, Any]:
    speech = (payload.get("speechText") or payload.get("spokenAnswer") or "").strip() if isinstance(payload, dict) else ""
    if not speech:
        title = (part or {}).get("title") or (roadmap or {}).get("title") or "this part"
        transcript = (part or {}).get("transcript_chunk") or ""
        summary = (part or {}).get("summary") or transcript or "this roadmap part introduces the next idea in the lesson."
        concepts = ", ".join((part or {}).get("concepts") or [])
        speech = f"Let's start with {title}. {summary}"
        if concepts:
            speech += f" The main ideas in this part are {concepts}."
        speech += " I will stay with this part before we move ahead."
    return {
        "speechText": _strip_followup_from_speech(speech, PART_UNDERSTANDING_QUESTION),
        "askFollowUp": PART_UNDERSTANDING_QUESTION,
    }


def _normalize_clarify_roadmap_part(
    payload: dict[str, Any],
    *,
    topic: str,
    part: dict[str, Any] | None,
    student_query: str,
) -> dict[str, Any]:
    speech_raw = payload.get("speech") if isinstance(payload, dict) else {}
    speech_text = ""
    speech_timestamps: list[dict[str, Any]] = []
    if isinstance(speech_raw, dict):
        speech_text = (speech_raw.get("text") or "").strip()
        speech_timestamps = _normalize_speech_timestamps(speech_raw.get("timestamps"), fallback_text=speech_text)
    if not speech_text and isinstance(payload, dict):
        speech_text = (payload.get("speechText") or payload.get("spokenAnswer") or "").strip()
    if not speech_text:
        title = (part or {}).get("title") or topic or "this part"
        summary = (part or {}).get("summary") or (part or {}).get("transcript_chunk") or ""
        speech_text = (
            f"Let's slow down on {title}. You asked: {student_query.strip() or 'this part'}. "
            f"The part is saying: {summary or 'focus on the current idea and connect each step before moving on.'} "
            f"I'll show it visually, then you can tell me if the same point is still unclear."
        )
    if not speech_timestamps:
        speech_timestamps = _normalize_speech_timestamps(None, fallback_text=speech_text)

    visual_raw = payload.get("visual") if isinstance(payload, dict) else {}
    visual = visual_raw if isinstance(visual_raw, dict) else {}
    visual_timestamps = _normalize_visual_timestamps(visual.get("timestamps"))
    manim_code = (visual.get("manimCode") or visual.get("code") or "").strip()
    if not _looks_like_direct_manim_code(manim_code):
        manim_code = _fallback_manim_code(topic=topic, part=part, student_query=student_query, speech_text=speech_text)

    sync_raw = payload.get("syncPlan") if isinstance(payload, dict) else {}
    sync_plan = sync_raw if isinstance(sync_raw, dict) else {}
    segments = [item for item in (sync_plan.get("segments") or []) if isinstance(item, dict)]
    if not segments:
        visual_cue = ((part or {}).get("suggested_visuals") or [(part or {}).get("title") or topic or "current part"])[0]
        segments = [{"speechText": speech_text, "visualCue": visual_cue, "startHint": 0}]
    if not visual_timestamps:
        visual_timestamps = [
            {
                "start": item.get("startHint") if isinstance(item.get("startHint"), (int, float)) else 0,
                "end": None,
                "cue": (item.get("visualCue") or item.get("cue") or "").strip(),
            }
            for item in segments[:6]
        ]

    return {
        "speech": {
            "text": _strip_followup_from_speech(speech_text, CLARIFICATION_FOLLOWUP),
            "timestamps": speech_timestamps,
        },
        "visual": {
            "visualNeeded": bool(visual.get("visualNeeded", True)),
            "visualType": "manim",
            "manimCode": manim_code,
            "timestamps": visual_timestamps,
        },
        "syncPlan": {
            "segments": [
                {
                    "speechText": (item.get("speechText") or item.get("text") or "").strip() or speech_text,
                    "visualCue": (item.get("visualCue") or item.get("cue") or "").strip(),
                    "startHint": item.get("startHint") if isinstance(item.get("startHint"), (int, float)) else 0,
                }
                for item in segments[:6]
            ]
        },
        "askFollowUp": CLARIFICATION_FOLLOWUP,
    }


def _normalize_speech_timestamps(raw: Any, *, fallback_text: str) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or item.get("speechText") or "").strip()
        start = item.get("start")
        end = item.get("end")
        normalized.append(
            {
                "start": start if isinstance(start, (int, float)) else 0.0,
                "end": end if isinstance(end, (int, float)) else None,
                "text": text or fallback_text,
            }
        )
    if normalized:
        return normalized
    return [{"start": 0.0, "end": None, "text": fallback_text}]


def _normalize_visual_timestamps(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        normalized.append(
            {
                "start": start if isinstance(start, (int, float)) else 0.0,
                "end": end if isinstance(end, (int, float)) else None,
                "cue": (item.get("cue") or item.get("visualCue") or "").strip(),
            }
        )
    return normalized


def _normalize_persona_only(payload: dict[str, Any], *, topic: str, student_query: str) -> dict[str, Any]:
    spoken = (payload.get("spokenAnswer") or "").strip()
    if not spoken:
        return _stub_answer(mode="persona_only", topic=topic, part=None, student_query=student_query)
    return {
        "spokenAnswer": spoken,
        "disclaimer": (payload.get("disclaimer") or "This topic isn't from the uploaded videos, but I can explain it in this teacher's style.").strip(),
        "visualNeeded": bool(payload.get("visualNeeded")),
        "visualType": _normalize_visual_type(payload.get("visualType")),
        "visualPrompt": (payload.get("visualPrompt") or "").strip(),
        "askFollowUp": (payload.get("askFollowUp") or "Want me to keep going, or pause for a question?").strip(),
    }


def _normalize_visual_type(raw: Any) -> str:
    val = (str(raw or "")).strip().lower()
    if val in {"manim", "tldraw", "none"}:
        return val
    return "none"


def _strip_followup_from_speech(speech: str, followup: str) -> str:
    cleaned = (speech or "").strip()
    if not cleaned:
        return ""
    normalized = cleaned.lower().rstrip(" .?!")
    target = followup.lower().rstrip(" .?!")
    if normalized.endswith(target):
        idx = cleaned.lower().rfind(followup.lower().split("?")[0])
        if idx >= 0:
            cleaned = cleaned[:idx].rstrip(" \n.?!")
    return cleaned.strip()


def _looks_like_direct_manim_code(code: str) -> bool:
    return bool((code or "").strip()) and direct_manim_validation_error(code) is None


def _fallback_manim_code(*, topic: str, part: dict[str, Any] | None, student_query: str, speech_text: str) -> str:
    title = json.dumps(((part or {}).get("title") or topic or "Roadmap part")[:44])
    cue = ((part or {}).get("suggested_visuals") or [(part or {}).get("summary") or student_query or speech_text])[0]
    caption = json.dumps(str(cue or "Visual explanation")[:86])
    concept_items = [(part or {}).get("title") or topic or "Current part"]
    concept_items.extend((part or {}).get("concepts") or [])
    concepts = json.dumps([str(item)[:34] for item in concept_items[:4]])
    return f'''from manim import *

{_MANIM_REGION_HELPERS_CODE}

class {MANIM_SCENE_CLASS_NAME}(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        active_regions = {{"title": None, "left": None, "right": None, "bottom": None}}
        title = safe_text({title}, font_size=34, max_width=12.0)
        place_title(title)
        replace_region(self, active_regions, "title", title)

        items = {concepts}
        left_panel = bullet_list(items, max_width=5.2, font_size=25)
        place_left(left_panel)
        replace_region(self, active_regions, "left", left_panel)

        cards = VGroup()
        for index, item in enumerate(items):
            box = Rectangle(width=1.55, height=0.88, color=ORANGE)
            label = safe_text(str(item), font_size=21, max_width=1.35)
            label.move_to(box.get_center())
            cards.add(VGroup(box, label))
        cards.arrange(DOWN, buff=0.18)
        fit_to_region(cards, 4.8, 4.2)
        arrows = VGroup()
        for index in range(max(0, len(cards) - 1)):
            arrows.add(Arrow(cards[index].get_bottom(), cards[index + 1].get_top(), buff=0.1, color=BLUE))
        diagram = VGroup(cards, arrows)
        place_right(diagram)
        replace_region(self, active_regions, "right", diagram)

        caption = safe_text({caption}, font_size=24, max_width=11.5)
        place_bottom(caption)
        replace_region(self, active_regions, "bottom", caption)

        follow = safe_text("Does that make sense now?", font_size=23, max_width=11.5)
        place_bottom(follow)
        replace_region(self, active_regions, "bottom", follow)
        self.wait(1.4)
'''
