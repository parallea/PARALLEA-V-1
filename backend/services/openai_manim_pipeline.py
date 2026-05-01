from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from config import OPENAI_API_KEY, PARALLEA_OPENAI_PIPELINE_MODEL
from backend.services.model_router import get_model_config, openai_uses_completion_tokens
from manim_renderer import direct_manim_validation_error, manim_allow_mathtex_effective_value, manim_mathtex_allowed

from .validators import clean_spaces, format_timecode, parse_timecode, sentence_case, trim_sentence

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - exercised when optional dependency is absent
    AsyncOpenAI = None


logger = logging.getLogger("parallea.openai-pipeline")

SCENE_CLASS_NAME = "ParalleaGeneratedScene"
MAX_SEGMENTS = 5
MIN_SEGMENTS = 3
OPENAI_MAX_OUTPUT_TOKENS = max(2500, int(os.getenv("PARALLEA_OPENAI_PIPELINE_MAX_TOKENS", "9000") or "9000"))
OPENAI_REASONING_EFFORT = clean_spaces(os.getenv("PARALLEA_OPENAI_REASONING_EFFORT", "medium")).lower()
REMOTE_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if OPENAI_API_KEY else "0") == "1"


def openai_pipeline_available() -> bool:
    return bool(REMOTE_ENABLED and OPENAI_API_KEY and AsyncOpenAI is not None)


def openai_pipeline_status() -> dict[str, Any]:
    cfg = get_model_config("visual")
    return {
        "provider": "openai",
        "model": cfg.model if cfg.provider == "openai" else PARALLEA_OPENAI_PIPELINE_MODEL,
        "available": openai_pipeline_available(),
        "api_key_loaded": bool(OPENAI_API_KEY),
        "sdk_loaded": AsyncOpenAI is not None,
    }


def openai_manim_response_schema() -> dict[str, Any]:
    segment = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "text": {"type": "string"},
            "purpose": {"type": "string"},
            "visual_cue": {"type": "string"},
        },
        "required": ["id", "start", "end", "text", "purpose", "visual_cue"],
    }
    frame = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "speech_segment_id": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "title": {"type": "string"},
            "scene_goal": {"type": "string"},
            "layout_notes": {"type": "string"},
            "duration_sec": {"type": "number"},
            "code": {"type": "string"},
        },
        "required": ["id", "speech_segment_id", "start", "end", "title", "scene_goal", "layout_notes", "duration_sec", "code"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "answer": {"type": "string"},
            "follow_up": {"type": "string"},
            "suggestions": {"type": "array", "items": {"type": "string"}},
            "formulae": {"type": "array", "items": {"type": "string"}},
            "speech": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"segments": {"type": "array", "items": segment}},
                "required": ["segments"],
            },
            "manim": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "scene_class_name": {"type": "string"},
                    "global_notes": {"type": "string"},
                    "frames": {"type": "array", "items": frame},
                },
                "required": ["scene_class_name", "global_notes", "frames"],
            },
        },
        "required": ["title", "answer", "follow_up", "suggestions", "formulae", "speech", "manim"],
    }


def persona_prompt(persona_context: str = "", pedagogy_mode: str = "simple") -> str:
    extra = clean_spaces(persona_context) or "Use a patient expert tutor persona with natural spoken delivery."
    return f"""
You are Parallea's single OpenAI lesson-to-Manim pipeline.
Persona:
{extra}

Teaching mode: {clean_spaces(pedagogy_mode) or "simple"}.
You produce the exact speech the tutor will say and the Manim code that appears with that speech.
The output is consumed directly by a renderer and text-to-speech system, so be concrete, visual, and concise.
""".strip()


def query_prompt(question: str, context: str, title: str, learner_request: str = "") -> str:
    return f"""
Topic title:
{trim_sentence(title, 160) or "Untitled lesson"}

Learner query:
{clean_spaces(learner_request) or clean_spaces(question)}

Canonical question to answer:
{clean_spaces(question)}

Context to use when relevant:
{trim_sentence(context, 3600) or "No additional source context was supplied."}
""".strip()


def generation_prompt() -> str:
    if manim_mathtex_allowed():
        latex_rules = """
- LaTeX is available and MathTex/Tex are allowed for equations only.
- Keep MathTex simple.
""".strip()
    else:
        latex_rules = f"""
- LaTeX is unavailable or MathTex is disabled ({manim_allow_mathtex_effective_value()}).
- Do NOT use MathTex, Tex, or SingleStringMathTex.
- Use Text("v = u + at") style formulas.
""".strip()
    return f"""
Return JSON only.

Generate two synchronized products:
1. `speech.segments`: exactly {MIN_SEGMENTS}-{MAX_SEGMENTS} natural voice-over segments with `HH:MM:SS` start/end timestamps.
2. `manim.frames`: one self-contained Manim Community Python clip for each speech segment, with matching timestamps.

Manim code rules:
- Every frame's `code` must be a full Python file, not a snippet.
- The scene class name must be `{SCENE_CLASS_NAME}` and must subclass `Scene`.
- Use `from manim import *` as the only import.
- No markdown fences, no external files, no images, no network, no shell, no file I/O.
- Prefer robust Manim primitives: Text, VGroup, Circle, Rectangle, Arrow, Line, NumberLine, Axes, Dot.
{latex_rules}
- Do NOT use Color(...), ManimColor(...), hsl=, rgb_to_color, from colour, import colour, or from manim.utils.color.
- Use only built-in color constants: WHITE, BLACK, BLUE, BLUE_E, GREEN, GREEN_E, RED, RED_E, YELLOW, ORANGE, PURPLE, GREY, GRAY.
- Fit all text inside a 16:9 scene. Keep text short and readable.
- The clip duration should match `duration_sec` using `run_time` and `self.wait`.
- Visuals should teach the idea, not decorate it.

Speech rules:
- Speak like a person, not a textbook.
- Keep each segment short enough for TTS.
- The full `answer` must be the concatenation-style summary of the speech.
- Timestamps must be monotonic and start at 00:00:00.
""".strip()


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text)
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            value = getattr(content, "text", None)
            if value:
                chunks.append(str(value))
    return "\n".join(chunks)


def _parse_json_preserving_code(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


async def call_openai_manim_pipeline(
    *,
    question: str,
    context: str,
    title: str,
    learner_request: str = "",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
) -> dict[str, Any] | None:
    if not openai_pipeline_available():
        return None
    assert AsyncOpenAI is not None
    cfg = get_model_config("visual")
    model = cfg.model if cfg.provider == "openai" else PARALLEA_OPENAI_PIPELINE_MODEL
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    text_format = {
        "type": "json_schema",
        "name": "parallea_openai_manim_pipeline",
        "schema": openai_manim_response_schema(),
        "strict": True,
    }
    request: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": persona_prompt(persona_context, pedagogy_mode)},
            {"role": "user", "content": query_prompt(question, context, title, learner_request)},
            {"role": "user", "content": generation_prompt()},
        ],
        "text": {"format": text_format, "verbosity": "medium"},
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
    }
    if openai_uses_completion_tokens(model) and OPENAI_REASONING_EFFORT in {"none", "low", "medium", "high", "xhigh"}:
        request["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}
    response = await client.responses.create(**request)
    parsed = _parse_json_preserving_code(_extract_response_text(response))
    return parsed if isinstance(parsed, dict) and parsed else None


def _segment_duration(text: str) -> int:
    words = max(1, len(clean_spaces(text).split()))
    return max(5, min(12, round(words / 2.4) + 2))


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _split_fallback_segments(question: str, context: str) -> list[str]:
    base = clean_spaces(question) or "this idea"
    context_hint = trim_sentence(context, 180)
    return [
        f"Let's make {base} visible first, with the main idea in the center.",
        f"The key move is to connect the parts step by step, so each piece has a clear job.",
        f"Now we compress that into one takeaway you can reuse: focus on the relationship, then apply it.",
    ] if not context_hint else [
        f"Let's answer {base} using the useful part of the lesson context.",
        f"The context points to this main relationship: {context_hint}",
        f"Here is the clean takeaway: name the parts, connect them, then apply the pattern.",
    ]


def _fallback_code(title: str, caption: str, duration: int, index: int) -> str:
    title_literal = json.dumps(trim_sentence(title, 44) or "Parallea")
    caption_literal = json.dumps(trim_sentence(caption, 92) or "Visual explanation")
    color = ["BLUE", "GREEN", "ORANGE", "PURPLE", "YELLOW"][index % 5]
    return f'''from manim import *

class {SCENE_CLASS_NAME}(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text({title_literal}, font_size=34, color=WHITE)
        title.to_edge(UP, buff=0.45)
        panel = Rectangle(width=11.2, height=4.8, color={color})
        caption = Text({caption_literal}, font_size=28, color=WHITE, line_spacing=0.9)
        caption.scale_to_fit_width(9.8)
        caption.move_to(panel.get_center())
        dot = Dot(color={color}).next_to(caption, LEFT, buff=0.35)
        self.play(FadeIn(title, shift=UP * 0.12), Create(panel), run_time=0.8)
        self.play(FadeIn(dot), Write(caption), run_time=1.2)
        self.play(panel.animate.set_stroke({color}, width=5), run_time=0.5)
        self.wait({max(1, duration - 3):.1f})
'''


def fallback_openai_manim_output(question: str, context: str, title: str) -> dict[str, Any]:
    texts = _split_fallback_segments(question, context)
    cursor = 0
    speech_segments: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    for index, text in enumerate(texts, start=1):
        duration = _segment_duration(text)
        start = format_timecode(cursor)
        end = format_timecode(cursor + duration)
        segment_id = f"segment_{index}"
        frame_id = f"frame_{index}"
        speech_segments.append(
            {
                "id": segment_id,
                "start": start,
                "end": end,
                "text": sentence_case(text),
                "purpose": "core_explanation",
                "visual_cue": trim_sentence(text, 120),
            }
        )
        frames.append(
            {
                "id": frame_id,
                "speech_segment_id": segment_id,
                "start": start,
                "end": end,
                "title": trim_sentence(title or question, 52),
                "scene_goal": sentence_case(trim_sentence(text, 160)),
                "layout_notes": "Single focused classroom visual.",
                "duration_sec": duration,
                "code": _fallback_code(title or question, text, duration, index),
            }
        )
        cursor += duration
    return {
        "title": trim_sentence(title or question, 72),
        "answer": " ".join(item["text"] for item in speech_segments),
        "follow_up": "Which part should I make more visual?",
        "suggestions": ["Explain more slowly", "Show another example", "Repeat the key idea"],
        "formulae": [],
        "speech": {"segments": speech_segments},
        "manim": {"scene_class_name": SCENE_CLASS_NAME, "global_notes": "Local fallback used.", "frames": frames},
    }


def _sanitize_manim_code(code: Any, *, fallback_code: str) -> tuple[str, list[str]]:
    raw = str(code or "").strip()
    warnings: list[str] = []
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:python)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    if f"class {SCENE_CLASS_NAME}(Scene)" not in raw:
        warnings.append("Generated Manim code did not declare the required scene class; fallback code used.")
        return fallback_code, warnings
    if "from manim import *" not in raw:
        raw = "from manim import *\n\n" + raw
    validation_error = direct_manim_validation_error(raw, scene_class_name=SCENE_CLASS_NAME)
    if validation_error:
        return fallback_code, [f"Generated Manim code failed validation: {validation_error}; fallback code used."]
    return raw, warnings


def normalize_openai_manim_output(raw: dict[str, Any], *, question: str, context: str, title: str) -> dict[str, Any]:
    fallback = fallback_openai_manim_output(question, context, title)
    parsed = raw if isinstance(raw, dict) else fallback
    speech_raw = ((parsed.get("speech") or {}).get("segments") or []) if isinstance(parsed.get("speech"), dict) else []
    frame_raw = ((parsed.get("manim") or {}).get("frames") or []) if isinstance(parsed.get("manim"), dict) else []
    if not speech_raw or not frame_raw:
        parsed = fallback
        speech_raw = parsed["speech"]["segments"]
        frame_raw = parsed["manim"]["frames"]

    segments: list[dict[str, Any]] = []
    cursor = 0
    for index, item in enumerate(speech_raw[:MAX_SEGMENTS], start=1):
        if not isinstance(item, dict):
            continue
        text = sentence_case(trim_sentence(item.get("text"), 420))
        if not text:
            continue
        start = parse_timecode(item.get("start"))
        end = parse_timecode(item.get("end"))
        if start < cursor:
            start = cursor
        if end <= start:
            end = start + _segment_duration(text)
        cursor = end
        segments.append(
            {
                "id": clean_spaces(item.get("id")) or f"segment_{index}",
                "start": format_timecode(start),
                "end": format_timecode(end),
                "text": text,
                "purpose": clean_spaces(item.get("purpose")).lower() or "core_explanation",
                "visual_cue": sentence_case(trim_sentence(item.get("visual_cue") or text, 140)),
            }
        )
    if len(segments) < MIN_SEGMENTS:
        return normalize_openai_manim_output(fallback, question=question, context=context, title=title)

    frame_by_segment = {
        clean_spaces(item.get("speech_segment_id")): item
        for item in frame_raw
        if isinstance(item, dict) and clean_spaces(item.get("speech_segment_id"))
    }
    frames: list[dict[str, Any]] = []
    code_warnings: list[str] = []
    for index, segment in enumerate(segments, start=1):
        item = frame_by_segment.get(segment["id"])
        if not isinstance(item, dict) and index - 1 < len(frame_raw) and isinstance(frame_raw[index - 1], dict):
            item = frame_raw[index - 1]
        item = item if isinstance(item, dict) else {}
        start_seconds = parse_timecode(segment["start"])
        end_seconds = parse_timecode(segment["end"])
        duration = max(1, end_seconds - start_seconds)
        fallback_code = _fallback_code(title or question, segment["visual_cue"], duration, index)
        code, warnings = _sanitize_manim_code(item.get("code"), fallback_code=fallback_code)
        code_warnings.extend(warnings)
        frames.append(
            {
                "id": clean_spaces(item.get("id")) or f"frame_{index}",
                "speech_segment_id": segment["id"],
                "start": segment["start"],
                "end": segment["end"],
                "title": trim_sentence(item.get("title") or parsed.get("title") or title or question, 56),
                "scene_goal": sentence_case(trim_sentence(item.get("scene_goal") or segment["visual_cue"], 220)),
                "layout_notes": sentence_case(trim_sentence(item.get("layout_notes") or "Focused Manim visual synced to narration.", 180)),
                "duration_sec": _safe_float(item.get("duration_sec"), float(duration)),
                "code": code,
            }
        )

    answer = clean_spaces(parsed.get("answer")) or " ".join(item["text"] for item in segments)
    return {
        "title": trim_sentence(parsed.get("title") or title or question, 72),
        "answer": sentence_case(trim_sentence(answer, 1400)),
        "follow_up": sentence_case(trim_sentence(parsed.get("follow_up") or "What should I explain next?", 140)),
        "suggestions": [trim_sentence(item, 72) for item in (parsed.get("suggestions") or []) if clean_spaces(item)][:4]
        or ["Explain more slowly", "Show another example", "Repeat the key idea"],
        "formulae": [trim_sentence(item, 140) for item in (parsed.get("formulae") or []) if clean_spaces(item)][:6],
        "speech": {"segments": segments},
        "manim": {
            "scene_class_name": SCENE_CLASS_NAME,
            "global_notes": trim_sentence((parsed.get("manim") or {}).get("global_notes") if isinstance(parsed.get("manim"), dict) else "", 220),
            "frames": frames,
        },
        "debug": {"codeWarnings": code_warnings},
    }
