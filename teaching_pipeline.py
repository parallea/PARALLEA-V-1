from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from ai_prompts import (
    EXCALIDRAW_RENDER_PROMPT,
    GEMINI_SEGMENTATION_PROMPT,
    NON_VIDEO_CONTEXT_MODE_PROMPT,
    PEDAGOGY_ADVANCE_PROMPT,
    PEDAGOGY_CLARIFY_PROMPT,
    PEDAGOGY_CONFIRM_ADVANCE_PROMPT,
    PEDAGOGY_DETAILED_PROMPT,
    PEDAGOGY_SIMPLE_PROMPT,
    MANIM_RENDER_PROMPT,
    RENDER_MODE_SELECTION_PROMPT,
    TEACHING_PLAN_PROMPT,
    VIDEO_CONTEXT_MODE_PROMPT,
)
from blackboard_visuals import build_blackboard_visual_payload
from board_asset_library import (
    BOARD_ASSETS,
    asset_file,
    excalidraw_asset_library_text,
    is_valid_asset_name,
    suggest_excalidraw_assets,
)
from board_elements import board_element_library_text
from board_scene_library import is_valid_scene_object, is_valid_scene_slot
from backend.visuals.manim_renderer import render_manim_payload_async, select_storyboard_scene, storyboard_scene_to_payload
from backend.visuals.visual_planner import build_visual_storyboard
from backend.services.question_pipeline import build_question_pipeline
from backend.services.presentation_sync import build_visual_payload as build_synced_visual_payload
from config import GEMINI_API_KEY, GROQ_API_KEY, VISUAL_PIPELINE
from gemini_service import build_gemini_client, generate_json_with_retry
from groq_service import build_async_groq_client, chat_completion_json_with_retry
from manim_renderer import heuristic_manim_payload, manim_relevance_score, normalize_manim_payload
from model_routing import resolve_gemini_model_config, resolve_groq_model_config


REMOTE_TEACHER_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if GEMINI_API_KEY else "0") == "1"
TEACHING_MODEL_CONFIG = resolve_gemini_model_config(
    "PARALLEA_GEMINI_TEACHING_MODEL",
    default="gemini-2.5-flash",
    label="teaching-pipeline",
)
TEACHING_MODEL = TEACHING_MODEL_CONFIG["model"]
FRAME_PLANNER_MODEL_CONFIG = resolve_gemini_model_config(
    "PARALLEA_GEMINI_FRAME_MODEL",
    fallback_envs=["PARALLEA_FRAME_PLANNER_MODEL", "PARALLEA_GEMINI_TEACHING_MODEL"],
    default=TEACHING_MODEL,
    label="frame-planner",
)
FRAME_PLANNER_MODEL = FRAME_PLANNER_MODEL_CONFIG["model"]
GEMINI_SEGMENT_MODEL_CONFIG = resolve_gemini_model_config(
    "PARALLEA_GEMINI_SEGMENT_MODEL",
    fallback_envs=["PARALLEA_GEMINI_TEACHING_MODEL"],
    default=TEACHING_MODEL,
    label="segment-planner",
)
GEMINI_SEGMENT_MODEL = GEMINI_SEGMENT_MODEL_CONFIG["model"]
PROMPT_JSON_LIMIT = max(600, int(os.getenv("PARALLEA_PROMPT_JSON_LIMIT", os.getenv("PARALLEA_GROQ_PROMPT_JSON_LIMIT", "2200")) or "2200"))
CONTEXT_LIMIT = max(800, int(os.getenv("PARALLEA_CONTEXT_LIMIT", os.getenv("PARALLEA_GROQ_CONTEXT_LIMIT", "2800")) or "2800"))
LESSON_MAX_TOKENS = max(300, int(os.getenv("PARALLEA_LLM_LESSON_MAX_TOKENS", os.getenv("PARALLEA_GROQ_LESSON_MAX_TOKENS", "1400")) or "1400"))
FRAME_MAX_TOKENS = max(250, int(os.getenv("PARALLEA_LLM_FRAME_MAX_TOKENS", os.getenv("PARALLEA_GROQ_FRAME_MAX_TOKENS", "950")) or "950"))

gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=REMOTE_TEACHER_ENABLED)
groq_client = build_async_groq_client(GROQ_API_KEY, enabled=REMOTE_TEACHER_ENABLED)
logger = logging.getLogger("parallea.pipeline")

FIRST_ANSWER_MODEL_CONFIG = resolve_groq_model_config(
    "PARALLEA_GROQ_FIRST_ANSWER_MODEL",
    fallback_envs=["PARALLEA_GROQ_TEACHING_MODEL"],
    default="llama-3.3-70b-versatile",
    label="first-answer",
)
FIRST_ANSWER_MODEL = FIRST_ANSWER_MODEL_CONFIG["model"]

PACE_VALUES = {"slow", "medium", "brisk"}
RENDER_MODE_VALUES = {"excalidraw", "whiteboard", "manim"}
RENDER_MODE_ALIASES = {"whiteboard": "excalidraw"}
STATIC_RENDER_MODES = {"excalidraw", "whiteboard"}
ASSET_MOTIONS = {"float", "pulse", "drift"}
EXCALIDRAW_VISIBILITY_VALUES = {"primary", "supporting", "background", "muted"}
EXCALIDRAW_HIGHLIGHT_VALUES = {"none", "pulse", "outline", "glow"}

GEMINI_SEGMENTATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson_title": {"type": "string"},
        "segmentation_strategy": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "label": {"type": "string"},
                    "speech_text": {"type": "string"},
                    "frame_goal": {"type": "string"},
                    "timing_hint": {
                        "type": "object",
                        "properties": {
                            "target_duration_sec": {"type": "number"},
                            "pace": {"type": "string"},
                        },
                        "required": ["target_duration_sec", "pace"],
                    },
                },
                "required": ["segment_id", "speech_text", "frame_goal", "timing_hint"],
            },
        },
    },
    "required": ["lesson_title", "segments"],
}

MANIM_REASON_HINTS = {
    "motion or trajectory": ["motion", "move", "movement", "trajectory", "path", "travel", "orbit", "flow through"],
    "mathematical transformation": ["transform", "transformation", "equation", "algebra", "simplify", "derive", "solve"],
    "graph or curve evolution": ["graph", "plot", "curve", "function", "slope", "coordinate", "axis", "axes"],
    "geometry or spatial change": ["geometry", "triangle", "angle", "vector", "rotation", "translation", "projection"],
    "state change over time": ["state", "transition", "changes", "over time", "before and after", "progression", "sequence"],
    "physical process": ["force", "velocity", "acceleration", "oscillation", "periodic", "wave", "field"],
}

EXCALIDRAW_REASON_HINTS = {
    "architecture or system layout": ["architecture", "system", "service", "module", "component", "pipeline overview", "block diagram"],
    "labeled relationships": ["relationship", "depends on", "connects", "maps to", "linked", "hierarchy", "structure"],
    "static concept breakdown": ["overview", "parts", "labels", "compare parts", "diagram", "scene", "board"],
    "code or logic explanation": ["code", "logic", "control flow", "function", "class", "api", "request", "response"],
}


def clean_spaces(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_render_mode_name(mode: Any) -> str:
    value = clean_spaces(mode).lower()
    if value in RENDER_MODE_ALIASES:
        value = RENDER_MODE_ALIASES[value]
    return value


def segment_text_blob(question: str, lesson_plan: dict[str, Any], segment: dict[str, Any]) -> str:
    pieces = [
        question,
        lesson_plan.get("topic", ""),
        lesson_plan.get("answer_summary", ""),
        " ".join(lesson_plan.get("key_ideas") or []),
        segment.get("label", ""),
        segment.get("speech_text", ""),
        segment.get("frame_goal", ""),
    ]
    return clean_spaces(" ".join(str(item or "") for item in pieces)).lower()


def count_phrase_hits(text: str, phrases: list[str]) -> int:
    return sum(1 for phrase in phrases if phrase in text)


def matching_labels(text: str, mapping: dict[str, list[str]]) -> list[str]:
    labels = []
    for label, phrases in mapping.items():
        if any(phrase in text for phrase in phrases):
            labels.append(label)
    return labels


def trim_sentence(text: Any, limit: int = 140) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def sentence_case(text: Any) -> str:
    value = clean_spaces(text)
    if not value:
        return ""
    value = value[0].upper() + value[1:]
    if value[-1] not in ".!?":
        value += "."
    return value


def normalize_visual_mode_hint_value(value: Any) -> str:
    hint = clean_spaces(value).lower()
    return hint if hint in {"manim", "excalidraw"} else ""


def normalize_visualization_preference(value: Any) -> str:
    mode = clean_spaces(value).lower()
    return mode if mode in {"manim", "excalidraw"} else ""


def infer_visual_mode_hint(text: Any) -> str:
    blob = clean_spaces(text).lower()
    if not blob:
        return ""
    manim_hits = count_phrase_hits(blob, [phrase for phrases in MANIM_REASON_HINTS.values() for phrase in phrases])
    excalidraw_hits = count_phrase_hits(blob, [phrase for phrases in EXCALIDRAW_REASON_HINTS.values() for phrase in phrases])
    if manim_hits >= max(2, excalidraw_hits + 1):
        return "manim"
    if excalidraw_hits >= max(2, manim_hits + 1):
        return "excalidraw"
    return ""


def normalize_formula_terms(raw_terms: Any) -> list[dict[str, str]]:
    terms = []
    for item in raw_terms or []:
        if not isinstance(item, dict):
            continue
        term = trim_sentence(item.get("term"), 36)
        meaning = sentence_case(trim_sentence(item.get("meaning"), 120))
        if not term or not meaning:
            continue
        terms.append({"term": term, "meaning": meaning})
    return terms[:4]


def normalize_key_formulas(raw_formulas: Any) -> list[dict[str, str]]:
    formulas = []
    for item in raw_formulas or []:
        if not isinstance(item, dict):
            continue
        formula = trim_sentence(item.get("formula"), 80)
        meaning = sentence_case(trim_sentence(item.get("meaning"), 140))
        when_to_use = sentence_case(trim_sentence(item.get("when_to_use"), 120))
        if not formula:
            continue
        formulas.append(
            {
                "formula": formula,
                "meaning": meaning or "Use this to describe the relationship clearly.",
                "when_to_use": when_to_use or "Use it when this relationship matters to the explanation.",
            }
        )
    return formulas[:4]


def split_sentences(text: Any, limit: int = 4) -> list[str]:
    parts = [clean_spaces(part) for part in re.split(r"(?<=[.!?])\s+", clean_spaces(text)) if clean_spaces(part)]
    if not parts and clean_spaces(text):
        parts = [clean_spaces(text)]
    return [sentence_case(trim_sentence(part, 220)) for part in parts[:limit]]


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def compact_json(value: Any, limit: int = 1600) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except Exception:
        raw = str(value)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "..."


def prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def prompt_safe_text(text: Any, limit: int) -> str:
    return trim_sentence(clean_spaces(text), limit)


def lesson_plan_prompt_view(lesson_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": prompt_safe_text(lesson_plan.get("topic"), 80),
        "teaching_objective": prompt_safe_text(lesson_plan.get("teaching_objective"), 220),
        "answer_summary": prompt_safe_text(lesson_plan.get("answer_summary"), 320),
        "teaching_style": prompt_safe_text(lesson_plan.get("teaching_style"), 160),
        "key_ideas": [prompt_safe_text(item, 120) for item in (lesson_plan.get("key_ideas") or []) if clean_spaces(item)],
        "visualization_notes": [prompt_safe_text(item, 120) for item in (lesson_plan.get("visualization_notes") or []) if clean_spaces(item)],
        "key_formulas": [
            {
                "formula": prompt_safe_text(item.get("formula"), 80),
                "meaning": prompt_safe_text(item.get("meaning"), 140),
                "when_to_use": prompt_safe_text(item.get("when_to_use"), 120),
            }
            for item in (lesson_plan.get("key_formulas") or [])
            if isinstance(item, dict) and clean_spaces(item.get("formula"))
        ],
        "examples": [prompt_safe_text(item, 140) for item in (lesson_plan.get("examples") or []) if clean_spaces(item)],
        "teaching_steps": [
            {
                "step_id": clean_spaces(step.get("step_id")) or f"step_{idx}",
                "label": prompt_safe_text(step.get("label"), 60),
                "key_idea": prompt_safe_text(step.get("key_idea"), 180),
                "explanation": prompt_safe_text(step.get("explanation"), 260),
                "visual_focus": prompt_safe_text(step.get("visual_focus"), 180),
                "example": prompt_safe_text(step.get("example"), 120),
                "formula": prompt_safe_text(step.get("formula"), 80),
                "formula_terms": [
                    {
                        "term": prompt_safe_text(item.get("term"), 36),
                        "meaning": prompt_safe_text(item.get("meaning"), 120),
                    }
                    for item in (step.get("formula_terms") or [])
                    if isinstance(item, dict) and clean_spaces(item.get("term")) and clean_spaces(item.get("meaning"))
                ],
                "visual_mode_hint": normalize_visual_mode_hint_value(step.get("visual_mode_hint")),
            }
            for idx, step in enumerate((lesson_plan.get("teaching_steps") or []), start=1)
            if isinstance(step, dict)
        ],
    }


def segment_prompt_view(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "segment_id": clean_spaces(segment.get("segment_id")),
        "step_id": clean_spaces(segment.get("step_id")),
        "label": prompt_safe_text(segment.get("label"), 64),
        "speech_text": prompt_safe_text(segment.get("speech_text"), 280),
        "frame_goal": prompt_safe_text(segment.get("frame_goal"), 220),
        "timing_hint": segment.get("timing_hint") if isinstance(segment.get("timing_hint"), dict) else {},
    }


def selection_prompt_view(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame_number": selection.get("frame_number"),
        "segment_id": clean_spaces(selection.get("segment_id")),
        "render_mode": clean_spaces(selection.get("render_mode")),
        "reason": prompt_safe_text(selection.get("reason"), 220),
        "scene_goal": prompt_safe_text(selection.get("scene_goal"), 220),
        "fallback_mode": clean_spaces(selection.get("fallback_mode")),
        "data_requirements": [prompt_safe_text(item, 120) for item in (selection.get("data_requirements") or []) if clean_spaces(item)][:4],
        "sync_notes": prompt_safe_text(selection.get("sync_notes"), 180),
    }


def clean_json(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    text = clean_spaces(raw)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else fallback
            except Exception:
                return fallback
    return fallback


def fill_prompt(template: str, values: dict[str, Any]) -> str:
    prompt = template
    for key, value in values.items():
        replacement = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        prompt = prompt.replace("{{" + key + "}}", replacement)
    return prompt


def context_mode_prompt(context_mode: str) -> str:
    return VIDEO_CONTEXT_MODE_PROMPT if clean_spaces(context_mode).lower() == "video_context" else NON_VIDEO_CONTEXT_MODE_PROMPT


def pedagogy_mode_prompt(pedagogy_mode: str) -> str:
    mode = clean_spaces(pedagogy_mode).lower()
    if mode == "detailed":
        return PEDAGOGY_DETAILED_PROMPT
    if mode == "clarify":
        return PEDAGOGY_CLARIFY_PROMPT
    if mode == "confirm_advance":
        return PEDAGOGY_CONFIRM_ADVANCE_PROMPT
    if mode == "advance":
        return PEDAGOGY_ADVANCE_PROMPT
    return PEDAGOGY_SIMPLE_PROMPT


def visual_depth_from_pedagogy_mode(pedagogy_mode: str) -> str:
    mode = clean_spaces(pedagogy_mode).lower()
    if mode in {"simple"}:
        return "brief"
    if mode in {"detailed", "advance", "confirm_advance"}:
        return "detailed"
    return "normal"


def history_snippet(conversation_history: list[dict[str, str]] | None, limit: int = 3) -> str:
    items = conversation_history or []
    rows = []
    for turn in items[-limit:]:
        role = clean_spaces(turn.get("role")).lower()
        if role not in {"user", "assistant"}:
            continue
        rows.append(f"{role}: {trim_sentence(turn.get('content'), 140)}")
    return "\n".join(rows) or "No prior conversation context."


def persona_guidance_block(persona_context: str) -> str:
    persona = clean_spaces(persona_context)
    if not persona:
        return ""
    return (
        "Instructor persona guidance:\n"
        f"Answer as if you are this instructor: {persona}. Mirror their vocabulary and explanation style."
    )


def answer_from_segments(
    lesson_plan: dict[str, Any],
    segment_plan: dict[str, Any],
    fallback_answer: str,
) -> tuple[str, list[dict[str, Any]], str]:
    segments = [item for item in (segment_plan.get("segments") or []) if isinstance(item, dict)]
    answer = " ".join(clean_spaces(segment.get("speech_text")) for segment in segments if clean_spaces(segment.get("speech_text"))).strip()
    if not answer:
        answer = clean_spaces(lesson_plan.get("answer_summary")) or clean_spaces(fallback_answer)
    first_segment_text = clean_spaces((segments[0] if segments else {}).get("speech_text"))
    return answer, segments, first_segment_text


def estimate_duration_seconds(text: str) -> float:
    words = len(clean_spaces(text).split())
    estimate = 3.8 + (words / 2.7)
    return round(clamp(estimate, 4.0, 10.0), 1)


async def call_model_json(
    prompt: str,
    model: str,
    fallback: dict[str, Any],
    *,
    operation: str,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not gemini_client:
        return fallback
    try:
        raw = await generate_json_with_retry(
            gemini_client,
            model=model,
            prompt=prompt,
            system_instruction="Return valid JSON only. Do not use markdown fences.",
            response_schema=response_schema,
            temperature=0.3,
            max_output_tokens=max_tokens,
            logger=logger,
            operation=operation,
        )
        return clean_json(raw, fallback)
    except Exception as exc:
        logger.exception(
            "Gemini pipeline call failed operation=%s prompt_chars=%s model=%s error=%s",
            operation,
            len(prompt),
            model,
            exc,
        )
        return fallback


async def call_first_answer_json(
    prompt: str,
    fallback: dict[str, Any],
    *,
    operation: str,
    max_tokens: int,
) -> dict[str, Any]:
    if groq_client:
        try:
            raw = await chat_completion_json_with_retry(
                groq_client,
                model=FIRST_ANSWER_MODEL,
                messages=[
                    {"role": "system", "content": "Return valid JSON only. Do not use markdown fences."},
                    {"role": "user", "content": prompt},
                ],
                logger=logger,
                operation=operation,
                temperature=0.25,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return clean_json(raw, fallback)
        except Exception as exc:
            logger.exception(
                "Groq first-answer call failed operation=%s prompt_chars=%s model=%s error=%s",
                operation,
                len(prompt),
                FIRST_ANSWER_MODEL,
                exc,
            )
    return await call_model_json(
        prompt,
        TEACHING_MODEL,
        fallback,
        operation=f"{operation}:gemini-fallback",
        max_tokens=max_tokens,
    )


def heuristic_lesson_plan(
    question: str,
    context: str,
    title: str,
    fallback_answer: str,
    fallback_follow_up: str,
    fallback_suggestions: list[str] | None,
) -> dict[str, Any]:
    summary_lines = split_sentences(fallback_answer or question, limit=8)
    context_lines = split_sentences(context, limit=2)
    steps = []
    source_lines = summary_lines or [sentence_case(question)]
    for idx, line in enumerate(source_lines, start=1):
        focus = context_lines[idx - 1] if idx - 1 < len(context_lines) else line
        steps.append(
            {
                "step_id": f"step_{idx}",
                "label": trim_sentence(f"Teaching beat {idx}", 40),
                "key_idea": line,
                "explanation": line,
                "visual_focus": focus,
                "example": "",
                "formula": "",
                "formula_terms": [],
                "visual_mode_hint": infer_visual_mode_hint(f"{line} {focus}"),
            }
        )
    if not steps:
        steps.append(
            {
                "step_id": "step_1",
                "label": "Core idea",
                "key_idea": sentence_case(question),
                "explanation": sentence_case(question),
                "visual_focus": sentence_case(question),
                "example": "",
                "formula": "",
                "formula_terms": [],
                "visual_mode_hint": infer_visual_mode_hint(question),
            }
        )
    return {
        "topic": trim_sentence(title or question, 72),
        "teaching_objective": sentence_case(fallback_answer or question),
        "answer_summary": " ".join(summary_lines) or sentence_case(fallback_answer or question),
        "teaching_style": "Build the idea one visible step at a time.",
        "key_ideas": summary_lines,
        "visualization_notes": context_lines[:2] or [sentence_case(fallback_answer or question)],
        "key_formulas": [],
        "examples": [],
        "teaching_steps": steps,
        "follow_up": clean_spaces(fallback_follow_up) or "What should I expand next?",
        "suggestions": [clean_spaces(item) for item in (fallback_suggestions or []) if clean_spaces(item)][:4],
    }


def normalize_lesson_plan(
    raw: dict[str, Any],
    question: str,
    context: str,
    title: str,
    fallback_answer: str,
    fallback_follow_up: str,
    fallback_suggestions: list[str] | None,
) -> dict[str, Any]:
    fallback = heuristic_lesson_plan(question, context, title, fallback_answer, fallback_follow_up, fallback_suggestions)
    if not isinstance(raw, dict):
        return fallback
    steps = []
    for idx, item in enumerate(raw.get("teaching_steps") or [], start=1):
        if not isinstance(item, dict):
            continue
        explanation = sentence_case(trim_sentence(item.get("explanation") or item.get("key_idea"), 260))
        key_idea = sentence_case(trim_sentence(item.get("key_idea") or explanation, 180))
        visual_focus = sentence_case(trim_sentence(item.get("visual_focus") or key_idea, 180))
        formula = trim_sentence(item.get("formula"), 80)
        formula_terms = normalize_formula_terms(item.get("formula_terms"))
        visual_mode_hint = normalize_visual_mode_hint_value(item.get("visual_mode_hint")) or infer_visual_mode_hint(
            f"{visual_focus} {formula} {key_idea}"
        )
        if not explanation:
            continue
        steps.append(
            {
                "step_id": clean_spaces(item.get("step_id")) or f"step_{idx}",
                "label": trim_sentence(clean_spaces(item.get("label")) or f"Step {idx}", 48),
                "key_idea": key_idea,
                "explanation": explanation,
                "visual_focus": visual_focus,
                "example": sentence_case(trim_sentence(item.get("example"), 180)) if clean_spaces(item.get("example")) else "",
                "formula": formula,
                "formula_terms": formula_terms,
                "visual_mode_hint": visual_mode_hint,
            }
        )
    if not steps:
        steps = fallback["teaching_steps"]
    suggestions = [clean_spaces(item) for item in (raw.get("suggestions") or []) if clean_spaces(item)]
    return {
        "topic": trim_sentence(clean_spaces(raw.get("topic")) or fallback["topic"], 72),
        "teaching_objective": sentence_case(trim_sentence(raw.get("teaching_objective") or fallback["teaching_objective"], 180)),
        "answer_summary": " ".join(split_sentences(raw.get("answer_summary") or fallback["answer_summary"], limit=4)),
        "teaching_style": sentence_case(trim_sentence(raw.get("teaching_style") or fallback["teaching_style"], 160)),
        "key_ideas": [sentence_case(trim_sentence(item, 160)) for item in (raw.get("key_ideas") or fallback["key_ideas"]) if clean_spaces(item)],
        "visualization_notes": [sentence_case(trim_sentence(item, 160)) for item in (raw.get("visualization_notes") or fallback["visualization_notes"]) if clean_spaces(item)],
        "key_formulas": normalize_key_formulas(raw.get("key_formulas")) or fallback["key_formulas"],
        "examples": [sentence_case(trim_sentence(item, 160)) for item in (raw.get("examples") or fallback["examples"]) if clean_spaces(item)],
        "teaching_steps": steps,
        "follow_up": clean_spaces(raw.get("follow_up")) or fallback["follow_up"],
        "suggestions": suggestions[:4] or fallback["suggestions"][:4],
    }


def heuristic_segment_plan(lesson_plan: dict[str, Any]) -> dict[str, Any]:
    segments = []
    steps = lesson_plan.get("teaching_steps") or []
    for idx, step in enumerate(steps, start=1):
        example = clean_spaces(step.get("example"))
        speech = clean_spaces(step.get("explanation"))
        formula = clean_spaces(step.get("formula"))
        formula_terms = [
            f"{clean_spaces(item.get('term'))} means {clean_spaces(item.get('meaning')).rstrip('.')}"
            for item in (step.get("formula_terms") or [])
            if isinstance(item, dict) and clean_spaces(item.get("term")) and clean_spaces(item.get("meaning"))
        ]
        if example:
            speech = f"{speech} {example}".strip()
        if formula:
            formula_tail = f" Keep the formula {formula} in view."
            if formula_terms:
                formula_tail += f" {' '.join(formula_terms[:2])}."
            speech = f"{speech} {formula_tail}".strip()
        frame_goal = clean_spaces(step.get("visual_focus") or step.get("key_idea"))
        if formula:
            frame_goal = f"{frame_goal} Show the formula {formula} and label the important quantities.".strip()
        segments.append(
            {
                "segment_id": f"segment_{idx}",
                "step_id": clean_spaces(step.get("step_id")) or f"step_{idx}",
                "label": trim_sentence(clean_spaces(step.get("label")) or f"Beat {idx}", 48),
                "speech_text": sentence_case(trim_sentence(speech or step.get("key_idea"), 320)),
                "frame_goal": sentence_case(trim_sentence(frame_goal or step.get("key_idea"), 220)),
                "timing_hint": {
                    "target_duration_sec": estimate_duration_seconds(speech or step.get("key_idea") or ""),
                    "pace": "medium",
                },
            }
        )
    if not segments:
        summary = lesson_plan.get("answer_summary") or lesson_plan.get("teaching_objective") or "Explain the core idea."
        segments = [
            {
                "segment_id": "segment_1",
                "step_id": "step_1",
                "label": "Core idea",
                "speech_text": sentence_case(trim_sentence(summary, 320)),
                "frame_goal": sentence_case(trim_sentence(summary, 200)),
                "timing_hint": {"target_duration_sec": estimate_duration_seconds(summary), "pace": "medium"},
            }
        ]
    return {
        "lesson_title": trim_sentence(lesson_plan.get("topic") or "Lesson", 56),
        "segmentation_strategy": "Move from the main idea to the supporting explanation step by step.",
        "segments": segments,
    }


def normalize_segment_plan(raw: dict[str, Any], lesson_plan: dict[str, Any]) -> dict[str, Any]:
    fallback = heuristic_segment_plan(lesson_plan)
    if not isinstance(raw, dict):
        return fallback
    seen_ids = set()
    segments = []
    lesson_steps = {clean_spaces(step.get("step_id")): step for step in (lesson_plan.get("teaching_steps") or [])}
    for idx, item in enumerate(raw.get("segments") or [], start=1):
        if not isinstance(item, dict):
            continue
        speech_text = sentence_case(trim_sentence(item.get("speech_text"), 360))
        if not speech_text:
            step = lesson_steps.get(clean_spaces(item.get("step_id")))
            speech_text = sentence_case(trim_sentence((step or {}).get("explanation"), 360))
        if not speech_text:
            continue
        timing_hint = item.get("timing_hint") if isinstance(item.get("timing_hint"), dict) else {}
        pace = clean_spaces(timing_hint.get("pace")).lower()
        if pace not in PACE_VALUES:
            pace = "medium"
        target_duration_sec = safe_float(timing_hint.get("target_duration_sec"), estimate_duration_seconds(speech_text))
        segment_id = clean_spaces(item.get("segment_id")) or f"segment_{idx}"
        if segment_id in seen_ids:
            segment_id = f"segment_{idx}"
        seen_ids.add(segment_id)
        step = lesson_steps.get(clean_spaces(item.get("step_id")), {})
        fallback_goal = clean_spaces(step.get("visual_focus") or speech_text)
        if clean_spaces(step.get("formula")):
            fallback_goal = f"{fallback_goal} Show the formula {clean_spaces(step.get('formula'))} with labeled quantities."
        frame_goal = sentence_case(trim_sentence(item.get("frame_goal") or fallback_goal or speech_text, 220))
        segments.append(
            {
                "segment_id": segment_id,
                "step_id": clean_spaces(item.get("step_id")) or clean_spaces(step.get("step_id")) or f"step_{idx}",
                "label": trim_sentence(clean_spaces(item.get("label")) or clean_spaces(step.get("label")) or f"Beat {idx}", 52),
                "speech_text": speech_text,
                "frame_goal": frame_goal,
                "timing_hint": {
                    "target_duration_sec": round(clamp(target_duration_sec, 4.0, 12.0), 1),
                    "pace": pace,
                },
            }
        )
    if not segments:
        segments = fallback["segments"]
    return {
        "lesson_title": trim_sentence(clean_spaces(raw.get("lesson_title")) or fallback["lesson_title"], 56),
        "segmentation_strategy": sentence_case(trim_sentence(raw.get("segmentation_strategy") or fallback["segmentation_strategy"], 180)),
        "segments": segments,
    }


def build_mode_data_requirements(render_mode: str) -> list[str]:
    if render_mode == "manim":
        return [
            "Choose one Manim scene template that matches the teaching beat.",
            "Provide structured scene parameters for objects, movement, graphing, or transformation.",
            "Include formulas, labels, axes, or key points when they teach the idea better.",
            "Keep timing aligned to the spoken segment duration.",
        ]
    return [
        "Choose reusable Excalidraw assets or semantic scene components from the available library.",
        "Provide structured object placement, labels, and visibility notes.",
        "Use readable labels or beat captions when a formula or named quantity matters.",
        "Keep the scene static-first and synchronized through beats/highlights instead of full animation.",
    ]


def heuristic_render_mode_selection(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    text = segment_text_blob(question, lesson_plan, segment)
    step = next(
        (
            item
            for item in (lesson_plan.get("teaching_steps") or [])
            if isinstance(item, dict) and clean_spaces(item.get("step_id")) == clean_spaces(segment.get("step_id"))
        ),
        {},
    )
    manim_labels = matching_labels(text, MANIM_REASON_HINTS)
    excalidraw_labels = matching_labels(text, EXCALIDRAW_REASON_HINTS)
    manim_score = manim_relevance_score(question, lesson_plan, segment) + (len(manim_labels) * 2)
    excalidraw_score = (len(excalidraw_labels) * 3) + len(
        suggest_excalidraw_assets(question, f"{segment.get('speech_text', '')} {segment.get('frame_goal', '')}", limit=3)
    )
    visual_hint = normalize_visual_mode_hint_value(step.get("visual_mode_hint"))
    if visual_hint == "manim":
        manim_score += 3
    elif visual_hint == "excalidraw":
        excalidraw_score += 3

    forced_mode = normalize_visualization_preference(preferred_visualization)
    render_mode = forced_mode or ("manim" if manim_score >= max(3, excalidraw_score) else "excalidraw")
    if render_mode == "manim":
        reason_bits = manim_labels[:2] or ["the concept becomes clearer when the learner can watch the change unfold"]
        fallback_mode = "excalidraw"
    else:
        reason_bits = excalidraw_labels[:2] or ["a labeled static scene is clearer than a full animation for this beat"]
        fallback_mode = "manim"

    if forced_mode:
        reason = sentence_case(f"Use {forced_mode} because the learner selected that visualization mode.")
    else:
        reason = sentence_case(" and ".join(reason_bits))
    duration = round(clamp(safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0), 4.0, 12.0), 1)
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
        "speech_segment_ref": segment.get("segment_id") or f"segment_{frame_number}",
        "render_mode": render_mode,
        "reason": reason,
        "scene_goal": sentence_case(trim_sentence(segment.get("frame_goal") or segment.get("speech_text"), 220)),
        "fallback_mode": fallback_mode,
        "data_requirements": build_mode_data_requirements(render_mode),
        "sync_notes": sentence_case(
            trim_sentence(
                f"Align the visual change to a {duration:.1f} second spoken beat and keep the main visual idea visible early.",
                180,
            )
        ),
    }


def normalize_render_mode_selection(
    raw: dict[str, Any],
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    fallback = heuristic_render_mode_selection(
        question,
        lesson_plan,
        segment,
        frame_number,
        preferred_visualization=preferred_visualization,
    )
    if not isinstance(raw, dict):
        return fallback
    render_mode = normalize_render_mode_name(raw.get("render_mode"))
    if render_mode not in {"excalidraw", "manim"}:
        render_mode = fallback["render_mode"]
    fallback_mode = normalize_render_mode_name(raw.get("fallback_mode"))
    if fallback_mode not in {"excalidraw", "manim"} or fallback_mode == render_mode:
        fallback_mode = fallback["fallback_mode"] if fallback["fallback_mode"] != render_mode else ("excalidraw" if render_mode == "manim" else "manim")
    data_requirements = [sentence_case(trim_sentence(item, 140)) for item in (raw.get("data_requirements") or []) if clean_spaces(item)][:4]
    if not data_requirements:
        data_requirements = fallback["data_requirements"]
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or fallback["segment_id"],
        "speech_segment_ref": clean_spaces(raw.get("speech_segment_ref")) or segment.get("segment_id") or fallback["speech_segment_ref"],
        "render_mode": render_mode,
        "reason": sentence_case(trim_sentence(raw.get("reason") or fallback["reason"], 220)),
        "scene_goal": sentence_case(trim_sentence(raw.get("scene_goal") or fallback["scene_goal"], 220)),
        "fallback_mode": fallback_mode,
        "data_requirements": data_requirements,
        "sync_notes": sentence_case(trim_sentence(raw.get("sync_notes") or fallback["sync_notes"], 180)),
    }


def sanitize_scene_object(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    kind = clean_spaces(obj.get("kind"))
    slot = clean_spaces(obj.get("slot"))
    if not is_valid_scene_object(kind) or not is_valid_scene_slot(slot):
        return None
    return {
        "id": clean_spaces(obj.get("id")) or f"{kind}_{slot}",
        "kind": kind,
        "slot": slot,
        "label": trim_sentence(clean_spaces(obj.get("label")) or kind.replace("_", " ").title(), 42),
        "detail": trim_sentence(clean_spaces(obj.get("detail")), 80),
    }


def sanitize_connector(connector: Any, valid_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(connector, dict):
        return None
    from_id = clean_spaces(connector.get("from"))
    to_id = clean_spaces(connector.get("to"))
    if from_id not in valid_ids or to_id not in valid_ids or from_id == to_id:
        return None
    return {
        "from": from_id,
        "to": to_id,
        "label": trim_sentence(clean_spaces(connector.get("label")) or "supports", 28),
    }


def sanitize_beat(beat: Any, valid_ids: set[str], idx: int) -> dict[str, Any] | None:
    if not isinstance(beat, dict):
        return None
    focus = [clean_spaces(item) for item in (beat.get("focus") or []) if clean_spaces(item) in valid_ids]
    if not focus:
        return None
    start_pct = clamp(safe_float(beat.get("start_pct"), 0.0), 0.0, 1.0)
    end_pct = clamp(safe_float(beat.get("end_pct"), 1.0), start_pct, 1.0)
    return {
        "id": clean_spaces(beat.get("id")) or f"beat_{idx}",
        "start_pct": start_pct,
        "end_pct": end_pct,
        "focus": focus[:3],
        "caption": trim_sentence(clean_spaces(beat.get("caption")) or "Notice the main idea.", 96),
    }


def sanitize_visual_assets(raw_assets: Any) -> list[dict[str, Any]]:
    assets = []
    seen = set()
    for idx, item in enumerate(raw_assets or [], start=1):
        if not isinstance(item, dict):
            continue
        name = clean_spaces(item.get("name"))
        slot = clean_spaces(item.get("slot"))
        if not is_valid_asset_name(name) or not is_valid_scene_slot(slot):
            continue
        signature = f"{name}:{slot}"
        if signature in seen:
            continue
        seen.add(signature)
        motion = clean_spaces(item.get("motion")).lower()
        if motion not in ASSET_MOTIONS:
            motion = BOARD_ASSETS[name]["motion"]
        assets.append(
            {
                "id": clean_spaces(item.get("id")) or f"asset_{idx}",
                "name": name,
                "url": f"/board-assets/{asset_file(name)}",
                "slot": slot,
                "label": trim_sentence(clean_spaces(item.get("label")) or BOARD_ASSETS[name]["label"], 28),
                "motion": motion,
            }
        )
    return assets[:3]


def build_excalidraw_object_placements(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    placements = []
    for idx, obj in enumerate(objects or []):
        if not isinstance(obj, dict):
            continue
        placements.append(
            {
                "object_id": clean_spaces(obj.get("id")) or f"obj_{idx + 1}",
                "kind": clean_spaces(obj.get("kind")),
                "slot": clean_spaces(obj.get("slot")),
                "label": trim_sentence(obj.get("label"), 42),
                "detail": trim_sentence(obj.get("detail"), 80),
                "visibility": "primary" if idx == 0 else "supporting",
                "highlight": "pulse" if idx == 0 else "outline",
            }
        )
    return placements[:3]


def build_excalidraw_asset_placements(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    placements = []
    for idx, asset in enumerate(assets or []):
        if not isinstance(asset, dict):
            continue
        placements.append(
            {
                "asset_id": clean_spaces(asset.get("name")),
                "slot": clean_spaces(asset.get("slot")),
                "label": trim_sentence(asset.get("label"), 28),
                "motion": clean_spaces(asset.get("motion")).lower() or BOARD_ASSETS.get(clean_spaces(asset.get("name")), {}).get("motion", "drift"),
                "visibility": "supporting" if idx else "primary",
            }
        )
    return placements[:3]


def sanitize_excalidraw_object_placements(raw_objects: Any, fallback_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    placements = []
    seen = set()
    for idx, item in enumerate(raw_objects or [], start=1):
        if not isinstance(item, dict):
            continue
        kind = clean_spaces(item.get("kind"))
        slot = clean_spaces(item.get("slot"))
        object_id = clean_spaces(item.get("object_id") or item.get("id")) or f"obj_{idx}"
        if not is_valid_scene_object(kind) or not is_valid_scene_slot(slot) or object_id in seen:
            continue
        seen.add(object_id)
        visibility = clean_spaces(item.get("visibility")).lower()
        if visibility not in EXCALIDRAW_VISIBILITY_VALUES:
            visibility = "primary" if idx == 1 else "supporting"
        highlight = clean_spaces(item.get("highlight")).lower()
        if highlight not in EXCALIDRAW_HIGHLIGHT_VALUES:
            highlight = "pulse" if idx == 1 else "outline"
        placements.append(
            {
                "object_id": object_id,
                "kind": kind,
                "slot": slot,
                "label": trim_sentence(item.get("label") or kind.replace("_", " ").title(), 42),
                "detail": trim_sentence(item.get("detail"), 80),
                "visibility": visibility,
                "highlight": highlight,
            }
        )
    return placements[:3] or build_excalidraw_object_placements(fallback_objects)


def sanitize_excalidraw_asset_placements(raw_assets: Any, fallback_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    placements = []
    seen = set()
    for idx, item in enumerate(raw_assets or [], start=1):
        if not isinstance(item, dict):
            continue
        asset_id = clean_spaces(item.get("asset_id") or item.get("name"))
        slot = clean_spaces(item.get("slot"))
        if not is_valid_asset_name(asset_id) or not is_valid_scene_slot(slot):
            continue
        signature = f"{asset_id}:{slot}"
        if signature in seen:
            continue
        seen.add(signature)
        motion = clean_spaces(item.get("motion")).lower()
        if motion not in ASSET_MOTIONS:
            motion = BOARD_ASSETS[asset_id]["motion"]
        visibility = clean_spaces(item.get("visibility")).lower()
        if visibility not in EXCALIDRAW_VISIBILITY_VALUES:
            visibility = "supporting"
        placements.append(
            {
                "asset_id": asset_id,
                "slot": slot,
                "label": trim_sentence(item.get("label") or BOARD_ASSETS[asset_id]["label"], 28),
                "motion": motion,
                "visibility": visibility,
            }
        )
    return placements[:3] or build_excalidraw_asset_placements(fallback_assets)


def inject_default_assets(question: str, segment: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    suggested = suggest_excalidraw_assets(question, f"{segment.get('speech_text', '')} {segment.get('frame_goal', '')}", limit=2)
    visual_assets = []
    slot_cycle = ["top_left", "top_right", "bottom_right"]
    for idx, name in enumerate(suggested):
        visual_assets.append(
            {
                "id": f"asset_{idx + 1}",
                "name": name,
                "url": f"/board-assets/{asset_file(name)}",
                "slot": slot_cycle[idx % len(slot_cycle)],
                "label": BOARD_ASSETS[name]["label"],
                "motion": BOARD_ASSETS[name]["motion"],
            }
        )
    payload["assets"] = sanitize_visual_assets(visual_assets)
    return payload


def build_semantic_scene_payload_for_segment(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    visual = build_blackboard_visual_payload(
        title=segment.get("label") or lesson_plan.get("topic") or "Lesson frame",
        question=question,
        answer=segment.get("speech_text") or lesson_plan.get("answer_summary") or question,
        focus_text=segment.get("frame_goal") or segment.get("speech_text") or question,
        supporting=(lesson_plan.get("visualization_notes") or lesson_plan.get("key_ideas") or [])[:2],
    )
    payload = dict(((visual.get("segments") or [{}])[0]).get("payload") or {})
    payload["title"] = trim_sentence(payload.get("title") or segment.get("label") or lesson_plan.get("topic") or "Lesson frame", 56)
    payload["subtitle"] = trim_sentence(payload.get("subtitle") or segment.get("frame_goal") or segment.get("speech_text"), 96)
    return inject_default_assets(question, segment, payload)


def heuristic_excalidraw_plan(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any]:
    payload = build_semantic_scene_payload_for_segment(question, lesson_plan, segment)
    objects = payload.get("objects") or []
    assets = payload.get("assets") or []
    labels = [trim_sentence(obj.get("label"), 42) for obj in objects if clean_spaces(obj.get("label"))]
    labels.extend(trim_sentence(asset.get("label"), 28) for asset in assets if clean_spaces(asset.get("label")))
    beats = payload.get("beats") or []
    actions = [sentence_case(trim_sentence(beat.get("caption"), 120)) for beat in beats if clean_spaces(beat.get("caption"))][:4]
    if not actions:
        actions = [
            "Reveal the central concept first.",
            "Add supporting labels only when they clarify the spoken beat.",
        ]
    object_placements = build_excalidraw_object_placements(objects)
    asset_placements = build_excalidraw_asset_placements(assets)
    payload["asset_ids"] = [asset.get("name") for asset in assets if clean_spaces(asset.get("name"))][:3]
    payload["labels"] = labels[:6]
    payload["object_placements"] = object_placements
    payload["asset_placements"] = asset_placements
    payload["actions"] = actions
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
        "scene_goal": sentence_case(trim_sentence(segment.get("frame_goal") or segment.get("speech_text"), 220)),
        "layout_notes": "Use a calm labeled board layout with reusable assets and semantic objects.",
        "selected_asset_ids": [asset.get("name") for asset in assets if clean_spaces(asset.get("name"))][:3],
        "labels": labels[:6],
        "object_placements": object_placements,
        "asset_placements": asset_placements,
        "actions": actions,
        "payload": payload,
    }


def normalize_whiteboard_payload(
    raw_payload: Any,
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    fallback = build_semantic_scene_payload_for_segment(question, lesson_plan, segment)
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    if payload.get("style") != "semantic_scene":
        return fallback
    objects = [cleaned for cleaned in (sanitize_scene_object(item) for item in (payload.get("objects") or [])) if cleaned]
    if not objects:
        return fallback
    unique_objects = []
    used_ids = set()
    used_slots = set()
    for obj in objects:
        if obj["id"] in used_ids or obj["slot"] in used_slots:
            continue
        used_ids.add(obj["id"])
        used_slots.add(obj["slot"])
        unique_objects.append(obj)
    if not unique_objects:
        return fallback
    valid_ids = {obj["id"] for obj in unique_objects}
    connectors = [cleaned for cleaned in (sanitize_connector(item, valid_ids) for item in (payload.get("connectors") or [])) if cleaned][:3]
    beats = [cleaned for cleaned in (sanitize_beat(item, valid_ids, idx + 1) for idx, item in enumerate(payload.get("beats") or [])) if cleaned]
    if not beats:
        beats = list(fallback.get("beats") or [])
    if beats:
        beats.sort(key=lambda item: item["start_pct"])
        cursor = 0.0
        for beat in beats:
            beat["start_pct"] = max(cursor, beat["start_pct"])
            beat["end_pct"] = max(beat["start_pct"], beat["end_pct"])
            cursor = beat["end_pct"]
        beats[-1]["end_pct"] = 1.0
    normalized = {
        "style": "semantic_scene",
        "title": trim_sentence(payload.get("title") or fallback.get("title"), 56),
        "subtitle": trim_sentence(payload.get("subtitle") or fallback.get("subtitle"), 96),
        "objects": unique_objects[:3],
        "connectors": connectors,
        "beats": beats[:4],
    }
    normalized["assets"] = sanitize_visual_assets(payload.get("assets")) or fallback.get("assets", [])
    return normalized


def normalize_excalidraw_plan(
    raw: dict[str, Any],
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any]:
    fallback = heuristic_excalidraw_plan(question, lesson_plan, segment, frame_number)
    if not isinstance(raw, dict):
        return fallback
    payload = normalize_whiteboard_payload(raw.get("payload"), question, lesson_plan, segment)
    object_placements = sanitize_excalidraw_object_placements(raw.get("object_placements"), payload.get("objects") or [])
    asset_placements = sanitize_excalidraw_asset_placements(raw.get("asset_placements"), payload.get("assets") or [])
    selected_asset_ids = [
        clean_spaces(item)
        for item in (raw.get("selected_asset_ids") or [])
        if is_valid_asset_name(clean_spaces(item))
    ][:3]
    if not selected_asset_ids:
        selected_asset_ids = [item.get("asset_id") for item in asset_placements if clean_spaces(item.get("asset_id"))][:3]
    if not selected_asset_ids:
        selected_asset_ids = fallback["selected_asset_ids"]
    labels = [trim_sentence(item, 42) for item in (raw.get("labels") or []) if clean_spaces(item)][:6] or fallback["labels"]
    actions = [sentence_case(trim_sentence(item, 140)) for item in (raw.get("actions") or []) if clean_spaces(item)][:4] or fallback["actions"]
    payload["assets"] = sanitize_visual_assets(payload.get("assets")) or fallback["payload"].get("assets", [])
    payload["asset_ids"] = selected_asset_ids
    payload["labels"] = labels
    payload["layout_notes"] = sentence_case(trim_sentence(raw.get("layout_notes") or fallback["layout_notes"], 200))
    payload["object_placements"] = object_placements
    payload["asset_placements"] = asset_placements
    payload["actions"] = actions
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or fallback["segment_id"],
        "scene_goal": sentence_case(trim_sentence(raw.get("scene_goal") or fallback["scene_goal"], 220)),
        "layout_notes": sentence_case(trim_sentence(raw.get("layout_notes") or fallback["layout_notes"], 200)),
        "selected_asset_ids": selected_asset_ids,
        "labels": labels,
        "object_placements": object_placements,
        "asset_placements": asset_placements,
        "actions": actions,
        "payload": payload,
    }


def heuristic_manim_plan(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any]:
    payload = heuristic_manim_payload(question, lesson_plan, segment)
    scene_type = payload.get("scene_type") or "concept_stack"
    animation_focus = [
        sentence_case(trim_sentence(segment.get("frame_goal") or segment.get("speech_text"), 140)),
        sentence_case(trim_sentence(f"Show the {scene_type.replace('_', ' ')} change clearly and early.", 140)),
    ]
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
        "scene_goal": sentence_case(trim_sentence(segment.get("frame_goal") or segment.get("speech_text"), 220)),
        "scene_type": scene_type,
        "animation_focus": animation_focus,
        "timing_notes": sentence_case("Start the main motion early and leave a short settle at the end of the spoken beat."),
        "payload": payload,
    }


def normalize_manim_plan(
    raw: dict[str, Any],
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any]:
    fallback = heuristic_manim_plan(question, lesson_plan, segment, frame_number)
    if not isinstance(raw, dict):
        return fallback
    payload = normalize_manim_payload(raw.get("payload"), question, lesson_plan, segment)
    animation_focus = [sentence_case(trim_sentence(item, 140)) for item in (raw.get("animation_focus") or []) if clean_spaces(item)][:4]
    if not animation_focus:
        animation_focus = fallback["animation_focus"]
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or fallback["segment_id"],
        "scene_goal": sentence_case(trim_sentence(raw.get("scene_goal") or fallback["scene_goal"], 220)),
        "scene_type": clean_spaces(raw.get("scene_type")) or payload.get("scene_type") or fallback["scene_type"],
        "animation_focus": animation_focus,
        "timing_notes": sentence_case(trim_sentence(raw.get("timing_notes") or fallback["timing_notes"], 180)),
        "payload": payload,
    }


def normalize_mermaid_payload(raw_payload: Any, segment: dict[str, Any]) -> dict[str, Any] | None:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    code = clean_spaces(payload.get("code") or payload.get("mermaid_code"))
    if not code:
        return None
    if "\n" not in code and not code.lower().startswith(("flowchart", "graph", "sequencediagram", "classdiagram", "mindmap", "timeline")):
        code = f"flowchart TD\nA[{segment.get('label') or 'Start'}] --> B[{segment.get('frame_goal') or 'Main idea'}]"
    return {"code": code}


def normalize_chartjs_payload(raw_payload: Any, segment: dict[str, Any]) -> dict[str, Any] | None:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    chart_type = clean_spaces(config.get("type"))
    data = config.get("data") if isinstance(config.get("data"), dict) else {}
    if chart_type and data:
        return {"config": config}
    labels = [segment.get("label") or "Current idea", "Takeaway"]
    return {
        "config": {
            "type": "bar",
            "data": {
                "labels": labels,
                "datasets": [
                    {
                        "label": segment.get("label") or "Lesson focus",
                        "data": [1, 0.7],
                        "backgroundColor": ["rgba(232,108,47,0.74)", "rgba(125,211,252,0.58)"],
                        "borderColor": ["#e86c2f", "#7dd3fc"],
                        "borderWidth": 1.5,
                    }
                ],
            },
            "options": {
                "responsive": True,
                "plugins": {"legend": {"display": False}},
                "scales": {
                    "x": {"ticks": {"color": "#e8e8e0"}},
                    "y": {"ticks": {"color": "#e8e8e0"}, "beginAtZero": True},
                },
            },
        }
    }


def build_frame_plan(
    selection: dict[str, Any],
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    renderer_payload: dict[str, Any],
    layout_notes: str,
    object_actions: list[str],
    visual_assets: list[dict[str, Any]],
    selected_asset_ids: list[str],
    fallback_payload: dict[str, Any],
    fallback_reason: str,
) -> dict[str, Any]:
    title = trim_sentence(segment.get("label") or lesson_plan.get("topic") or f"Frame {frame_number}", 52)
    render_mode = normalize_render_mode_name(selection.get("render_mode")) or "excalidraw"
    fallback_mode = normalize_render_mode_name(selection.get("fallback_mode")) or ("excalidraw" if render_mode == "manim" else "manim")
    return {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
        "speech_segment_ref": selection.get("speech_segment_ref") or segment.get("segment_id") or f"segment_{frame_number}",
        "title": title,
        "render_mode": render_mode,
        "reason": sentence_case(trim_sentence(selection.get("reason"), 220)),
        "scene_goal": sentence_case(trim_sentence(selection.get("scene_goal") or segment.get("frame_goal") or segment.get("speech_text"), 220)),
        "notes_for_sync": sentence_case(trim_sentence(selection.get("sync_notes"), 180)),
        "data_requirements": [sentence_case(trim_sentence(item, 140)) for item in (selection.get("data_requirements") or []) if clean_spaces(item)][:4],
        "layout_notes": sentence_case(trim_sentence(layout_notes, 200)),
        "object_actions": [sentence_case(trim_sentence(item, 140)) for item in (object_actions or []) if clean_spaces(item)][:4],
        "selected_asset_ids": selected_asset_ids[:3],
        "visual_assets": visual_assets[:3],
        "renderer_payload": renderer_payload,
        "payload": renderer_payload,
        "fallback_mode": fallback_mode,
        "fallback": {
            "render_mode": fallback_mode,
            "reason": sentence_case(trim_sentence(fallback_reason, 180)),
            "renderer_payload": fallback_payload,
            "payload": fallback_payload,
        },
    }


def heuristic_frame_plan(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
) -> dict[str, Any]:
    selection = heuristic_render_mode_selection(question, lesson_plan, segment, frame_number)
    excalidraw_plan = heuristic_excalidraw_plan(question, lesson_plan, segment, frame_number)
    manim_payload = heuristic_manim_payload(question, lesson_plan, segment)
    if selection["render_mode"] == "manim":
        return build_frame_plan(
            selection=selection,
            lesson_plan=lesson_plan,
            segment=segment,
            frame_number=frame_number,
            renderer_payload=manim_payload,
            layout_notes="Use motion to teach the transformation or progression, and keep the frame visually spare.",
            object_actions=[
                "Animate the changing relation early in the segment.",
                "Keep labels stable while the main mathematical or spatial change unfolds.",
            ],
            visual_assets=[],
            selected_asset_ids=[],
            fallback_payload=excalidraw_plan["payload"],
            fallback_reason="Use the Excalidraw-style semantic scene if animation fails or becomes unnecessary.",
        )
    return build_frame_plan(
        selection=selection,
        lesson_plan=lesson_plan,
        segment=segment,
        frame_number=frame_number,
        renderer_payload=excalidraw_plan["payload"],
        layout_notes=excalidraw_plan["layout_notes"],
        object_actions=excalidraw_plan["actions"],
        visual_assets=excalidraw_plan["payload"].get("assets", []),
        selected_asset_ids=excalidraw_plan["selected_asset_ids"],
        fallback_payload=manim_payload,
        fallback_reason="Use Manim if the static scene fails and the concept still benefits from visible progression.",
    )


def get_visual_payload(
    storyboard: dict[str, Any] | None,
    segment: dict[str, Any],
    fallback_allowed: bool = True,
    *,
    question: str = "",
    lesson_plan: dict[str, Any] | None = None,
    frame_number: int = 1,
    total_segments: int = 0,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    lesson_plan = lesson_plan or {}
    board = storyboard if isinstance(storyboard, dict) else {}
    forced_mode = normalize_visualization_preference(preferred_visualization)
    if forced_mode == "excalidraw":
        payload = build_semantic_scene_payload_for_segment(question, lesson_plan, segment)
        return {
            "path": "semantic_whiteboard",
            "render_mode": "excalidraw",
            "renderer_payload": payload,
            "reason": "Semantic whiteboard selected because the learner chose Excalidraw.",
            "fallback_payload": payload,
        }
    try:
        if isinstance(board.get("scene_sequence"), list) and board.get("scene_sequence"):
            scene = select_storyboard_scene(
                board,
                frame_number=frame_number,
                total_segments=total_segments,
                segment_id=clean_spaces(segment.get("segment_id")),
            )
            payload = storyboard_scene_to_payload(
                scene=scene,
                storyboard=board,
                question=question,
                lesson_plan=lesson_plan,
                segment=segment,
            )
            if isinstance(payload, dict) and payload:
                return {
                    "path": "storyboard_manim",
                    "render_mode": "manim",
                    "scene": scene,
                    "renderer_payload": payload,
                    "reason": f"Storyboard-first scene for {clean_spaces(getattr(scene, 'pedagogical_role', 'visual')) or 'visual'} teaching.",
                    "fallback_payload": build_semantic_scene_payload_for_segment(question, lesson_plan, segment),
                }
    except Exception as exc:
        logger.exception(
            "teaching-pipeline storyboard payload failed segment=%s frame=%s error=%s",
            segment.get("segment_id"),
            frame_number,
            exc,
        )
    if not fallback_allowed:
        return {"path": "none", "render_mode": "", "renderer_payload": {}}
    payload = build_semantic_scene_payload_for_segment(question, lesson_plan, segment)
    return {
        "path": "semantic_whiteboard",
        "render_mode": "excalidraw",
        "renderer_payload": payload,
        "reason": "Semantic whiteboard fallback after storyboard-first rendering was unavailable or empty.",
        "fallback_payload": payload,
    }


def build_storyboard_frame_plan(
    question: str,
    lesson_plan: dict[str, Any],
    segment_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    storyboard: dict[str, Any],
    preferred_visualization: str = "",
) -> dict[str, Any]:
    visual = get_visual_payload(
        storyboard,
        segment,
        fallback_allowed=True,
        question=question,
        lesson_plan=lesson_plan,
        frame_number=frame_number,
        total_segments=len(segment_plan.get("segments") or []),
        preferred_visualization=preferred_visualization,
    )
    logger.info(
        "teaching-pipeline visual-path segment=%s frame=%s path=%s",
        segment.get("segment_id"),
        frame_number,
        visual.get("path"),
    )
    payload = visual.get("renderer_payload") or {}
    if visual.get("path") != "storyboard_manim":
        selection = {
            "frame_number": frame_number,
            "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
            "speech_segment_ref": segment.get("segment_id") or f"segment_{frame_number}",
            "render_mode": "excalidraw",
            "fallback_mode": "excalidraw",
            "reason": visual.get("reason") or "Unified semantic whiteboard fallback.",
            "scene_goal": segment.get("frame_goal") or segment.get("speech_text") or segment.get("label"),
            "data_requirements": [
                "Use the semantic whiteboard payload because storyboard-driven Manim was unavailable or empty.",
                "Keep the board readable and aligned to the spoken segment.",
            ],
            "sync_notes": "Lead with the central concept and keep the supporting objects stable while the explanation plays.",
        }
        frame_plan = build_frame_plan(
            selection=selection,
            lesson_plan=lesson_plan,
            segment=segment,
            frame_number=frame_number,
            renderer_payload=payload,
            layout_notes="semantic whiteboard fallback",
            object_actions=["Reveal the central concept first.", "Keep support objects minimal and readable."],
            visual_assets=(payload.get("assets") or []),
            selected_asset_ids=[],
            fallback_payload=payload,
            fallback_reason="The semantic whiteboard is already the unified fallback path.",
        )
        frame_plan["visual_pipeline_path"] = visual.get("path")
        frame_plan["title"] = trim_sentence(payload.get("title") or frame_plan.get("title"), 52)
        return frame_plan

    scene = visual.get("scene")
    selection = {
        "frame_number": frame_number,
        "segment_id": segment.get("segment_id") or f"segment_{frame_number}",
        "speech_segment_ref": segment.get("segment_id") or f"segment_{frame_number}",
        "render_mode": "manim",
        "reason": visual.get("reason") or f"Storyboard-first scene for {clean_spaces(getattr(scene, 'pedagogical_role', 'visual')) or 'visual'} teaching.",
        "scene_goal": getattr(scene, "scene_goal", segment.get("frame_goal") or segment.get("speech_text")),
        "fallback_mode": "excalidraw",
        "data_requirements": [
            "Follow the validated storyboard scene instead of converting speech line by line.",
            "Use the subject builder to create the main visual objects and transformations.",
            "Keep on-screen text minimal and delay equations unless the scene explicitly asks for them.",
        ],
        "sync_notes": f"Match the scene rhythm to {getattr(scene, 'estimated_duration', 6.0):.1f} seconds and lead with the primary visual motion.",
    }
    semantic_fallback = visual.get("fallback_payload") or build_semantic_scene_payload_for_segment(question, lesson_plan, segment)
    frame_plan = build_frame_plan(
        selection=selection,
        lesson_plan=lesson_plan,
        segment=segment,
        frame_number=frame_number,
        renderer_payload=payload,
        layout_notes=f"{getattr(scene, 'layout_hint', 'center morph').replace('_', ' ')}. {getattr(scene, 'camera_behavior', 'steady framing')}",
        object_actions=list(getattr(scene, "animation_flow", []) or []),
        visual_assets=[],
        selected_asset_ids=[],
        fallback_payload=semantic_fallback,
        fallback_reason="Use the semantic scene fallback if storyboard-driven Manim rendering fails.",
    )
    frame_plan["storyboard_scene_id"] = getattr(scene, "scene_id", "")
    frame_plan["storyboard_scene_type"] = getattr(scene, "scene_type", "")
    frame_plan["storyboard_visual_strategy"] = storyboard.get("visual_strategy")
    frame_plan["visual_pipeline_path"] = visual.get("path")
    frame_plan["title"] = trim_sentence(payload.get("title") or frame_plan.get("title"), 52)
    return frame_plan


def frame_plan_to_visual_segment(frame_plan: dict[str, Any], start_pct: float, end_pct: float) -> dict[str, Any]:
    kind = normalize_render_mode_name(frame_plan.get("render_mode")) or "excalidraw"
    return {
        "id": frame_plan.get("segment_id") or f"segment_{frame_plan.get('frame_number', 1)}",
        "title": frame_plan.get("title") or f"Frame {frame_plan.get('frame_number', 1)}",
        "start_pct": round(start_pct, 4),
        "end_pct": round(end_pct, 4),
        "kind": kind,
        "payload": frame_plan.get("payload") or frame_plan.get("renderer_payload") or {},
        "renderer_payload": frame_plan.get("renderer_payload") or frame_plan.get("payload") or {},
        "frame_number": frame_plan.get("frame_number"),
        "speech_segment_ref": frame_plan.get("speech_segment_ref"),
        "reason": frame_plan.get("reason"),
        "scene_goal": frame_plan.get("scene_goal"),
        "layout_notes": frame_plan.get("layout_notes"),
        "notes_for_sync": frame_plan.get("notes_for_sync"),
        "selected_asset_ids": frame_plan.get("selected_asset_ids") or [],
        "fallback_mode": frame_plan.get("fallback_mode"),
        "fallback": frame_plan.get("fallback"),
    }


def build_visual_payload(segment_plan: dict[str, Any], frame_sequence: list[dict[str, Any]]) -> dict[str, Any]:
    segments = segment_plan.get("segments") or []
    if not frame_sequence or not segments:
        return {"segments": []}
    total_duration = sum(
        max(4.0, safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0))
        for segment in segments
    )
    cursor = 0.0
    visual_segments = []
    for segment, frame in zip(segments, frame_sequence):
        duration = max(4.0, safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0))
        start_pct = cursor / total_duration if total_duration else 0.0
        cursor += duration
        end_pct = cursor / total_duration if total_duration else 1.0
        visual_segments.append(frame_plan_to_visual_segment(frame, start_pct, end_pct))
    if visual_segments:
        visual_segments[0]["start_pct"] = 0.0
        visual_segments[-1]["end_pct"] = 1.0
    return {"segments": visual_segments}


async def materialize_frame_sequence(frame_sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    materialized = []
    for frame in frame_sequence:
        if not isinstance(frame, dict):
            continue
        if normalize_render_mode_name(frame.get("render_mode")) == "manim":
            try:
                source_payload = frame.get("renderer_payload") or frame.get("payload") or {}
                rendered = await render_manim_payload_async(
                    source_payload,
                    segment_id=clean_spaces(frame.get("segment_id")),
                    frame_number=int(frame.get("frame_number") or 0),
                )
                next_frame = dict(frame)
                next_frame["renderer_payload"] = source_payload
                next_frame["payload"] = {
                    "media_url": rendered.get("media_url"),
                    "scene_type": source_payload.get("scene_type"),
                    "title": frame.get("title"),
                    "subtitle": source_payload.get("subtitle"),
                    "duration_sec": source_payload.get("duration_sec"),
                    "scene_source_path": rendered.get("scene_source_path"),
                    "stderr_path": rendered.get("stderr_path"),
                    "stdout_path": rendered.get("stdout_path"),
                    "debug_meta_path": rendered.get("debug_meta_path"),
                }
                next_frame["render_output"] = rendered
                materialized.append(next_frame)
                logger.info(
                    "teaching-pipeline manim-render segment=%s frame=%s media=%s scene=%s",
                    frame.get("segment_id"),
                    frame.get("frame_number"),
                    rendered.get("media_url"),
                    rendered.get("scene_source_path"),
                )
                continue
            except Exception as exc:
                logger.exception(
                    "Manim materialization failed for segment=%s frame=%s scene=%s error=%s",
                    frame.get("segment_id"),
                    frame.get("frame_number"),
                    ((frame.get("payload") or {}).get("scene_source_path") or (frame.get("renderer_payload") or {}).get("scene_source_path")),
                    exc,
                )
                fallback_frame = dict(frame)
                fallback_block = frame.get("fallback") if isinstance(frame.get("fallback"), dict) else {}
                fallback_mode = normalize_render_mode_name(fallback_block.get("render_mode")) or "excalidraw"
                fallback_payload = fallback_block.get("renderer_payload") or fallback_block.get("payload")
                if fallback_mode not in {"excalidraw", "manim"}:
                    fallback_mode = "excalidraw"
                if fallback_mode == "manim" or not isinstance(fallback_payload, dict):
                    fallback_mode = "excalidraw"
                    fallback_payload = build_semantic_scene_payload_for_segment(
                        question=clean_spaces(frame.get("scene_goal") or frame.get("title")),
                        lesson_plan={"topic": frame.get("title"), "visualization_notes": [], "key_ideas": []},
                        segment={
                            "label": frame.get("title"),
                            "speech_text": frame.get("scene_goal"),
                            "frame_goal": frame.get("scene_goal"),
                        },
                    )
                fallback_frame["render_mode"] = fallback_mode
                fallback_frame["fallback_mode"] = fallback_mode
                fallback_frame["renderer_payload"] = fallback_payload
                fallback_frame["payload"] = fallback_payload
                fallback_frame["reason"] = sentence_case(
                    trim_sentence(
                        f"{frame.get('reason', '')} Fallback used because Manim rendering failed.",
                        220,
                    )
                )
                fallback_frame["render_error"] = trim_sentence(str(exc), 240)
                materialized.append(fallback_frame)
                logger.info(
                    "teaching-pipeline fallback-used segment=%s frame=%s mode=%s reason=%s",
                    frame.get("segment_id"),
                    frame.get("frame_number"),
                    fallback_mode,
                    fallback_block.get("reason"),
                )
                continue
        materialized.append(frame)
    return materialized


async def generate_structured_lesson(
    question: str,
    context: str,
    title: str,
    conversation_history: list[dict[str, str]] | None,
    fallback_answer: str,
    fallback_follow_up: str,
    fallback_suggestions: list[str] | None,
    *,
    learner_request: str | None = None,
    topic_question: str | None = None,
    context_mode: str = "video_context",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
) -> dict[str, Any]:
    fallback = heuristic_lesson_plan(question, context, title, fallback_answer, fallback_follow_up, fallback_suggestions)
    prompt = fill_prompt(
        TEACHING_PLAN_PROMPT,
        {
            "LEARNER_REQUEST": prompt_safe_text(learner_request or question, 240),
            "TOPIC_QUESTION": prompt_safe_text(topic_question or question, 240),
            "CONTEXT": prompt_safe_text(context or "No extra teaching context supplied.", CONTEXT_LIMIT),
            "SESSION_CONTEXT": history_snippet(conversation_history),
            "PERSONA_GUIDANCE_BLOCK": persona_guidance_block(persona_context),
            "MODE_GUIDANCE": context_mode_prompt(context_mode),
            "PEDAGOGY_GUIDANCE": pedagogy_mode_prompt(pedagogy_mode),
        },
    )
    raw = await call_first_answer_json(
        prompt,
        fallback,
        operation="structured-lesson",
        max_tokens=LESSON_MAX_TOKENS,
    )
    lesson_plan = normalize_lesson_plan(raw, question, context, title, fallback_answer, fallback_follow_up, fallback_suggestions)
    logger.info("teaching-pipeline lesson-question=%s pedagogy=%s context_mode=%s", trim_sentence(question, 220), pedagogy_mode, context_mode)
    logger.info("teaching-pipeline lesson-structured=%s", compact_json(lesson_plan))
    return lesson_plan


async def generate_segment_plan(question: str, lesson_plan: dict[str, Any]) -> dict[str, Any]:
    fallback = heuristic_segment_plan(lesson_plan)
    prompt = fill_prompt(
        GEMINI_SEGMENTATION_PROMPT,
        {
            "QUESTION": prompt_safe_text(question, 240),
            "LESSON_JSON": prompt_json(lesson_plan_prompt_view(lesson_plan)),
        },
    )
    raw = await call_model_json(
        prompt,
        GEMINI_SEGMENT_MODEL,
        fallback,
        operation="segment-plan",
        max_tokens=LESSON_MAX_TOKENS,
        response_schema=GEMINI_SEGMENTATION_JSON_SCHEMA,
    )
    segment_plan = normalize_segment_plan(raw, lesson_plan)
    logger.info("teaching-pipeline gemini-segments=%s", compact_json(segment_plan))
    return segment_plan


async def select_render_mode_for_segment(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    fallback = heuristic_render_mode_selection(
        question,
        lesson_plan,
        segment,
        frame_number,
        preferred_visualization=preferred_visualization,
    )
    if normalize_visualization_preference(preferred_visualization):
        logger.info(
            "teaching-pipeline mode-select frame=%s segment=%s mode=%s reason=%s fallback=%s",
            frame_number,
            fallback.get("segment_id"),
            fallback.get("render_mode"),
            fallback.get("reason"),
            fallback.get("fallback_mode"),
        )
        return fallback
    prompt = fill_prompt(
        RENDER_MODE_SELECTION_PROMPT,
        {
            "QUESTION": prompt_safe_text(question, 240),
            "LESSON_JSON": prompt_json(lesson_plan_prompt_view(lesson_plan)),
            "SEGMENT_JSON": prompt_json(segment_prompt_view(segment)),
        },
    )
    raw = await call_model_json(
        prompt,
        FRAME_PLANNER_MODEL,
        fallback,
        operation=f"render-mode-select:{segment.get('segment_id') or frame_number}",
        max_tokens=FRAME_MAX_TOKENS,
    )
    selection = normalize_render_mode_selection(
        raw,
        question,
        lesson_plan,
        segment,
        frame_number,
        preferred_visualization=preferred_visualization,
    )
    logger.info(
        "teaching-pipeline mode-select frame=%s segment=%s mode=%s reason=%s fallback=%s",
        frame_number,
        selection.get("segment_id"),
        selection.get("render_mode"),
        selection.get("reason"),
        selection.get("fallback_mode"),
    )
    return selection


async def plan_excalidraw_renderer_for_segment(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    selection: dict[str, Any],
) -> dict[str, Any]:
    fallback = heuristic_excalidraw_plan(question, lesson_plan, segment, frame_number)
    prompt = fill_prompt(
        EXCALIDRAW_RENDER_PROMPT,
        {
            "QUESTION": prompt_safe_text(question, 240),
            "LESSON_JSON": prompt_json(lesson_plan_prompt_view(lesson_plan)),
            "SEGMENT_JSON": prompt_json(segment_prompt_view(segment)),
            "MODE_SELECTION_JSON": prompt_json(selection_prompt_view(selection)),
            "EXCALIDRAW_ASSETS": excalidraw_asset_library_text(),
            "EXCALIDRAW_COMPONENTS": board_element_library_text(),
        },
    )
    raw = await call_model_json(
        prompt,
        FRAME_PLANNER_MODEL,
        fallback,
        operation=f"excalidraw-plan:{segment.get('segment_id') or frame_number}",
        max_tokens=FRAME_MAX_TOKENS,
    )
    plan = normalize_excalidraw_plan(raw, question, lesson_plan, segment, frame_number)
    logger.info(
        "teaching-pipeline excalidraw-plan segment=%s assets=%s payload=%s",
        segment.get("segment_id"),
        plan.get("selected_asset_ids"),
        compact_json(plan.get("payload")),
    )
    return plan


async def plan_manim_renderer_for_segment(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    selection: dict[str, Any],
) -> dict[str, Any]:
    fallback = heuristic_manim_plan(question, lesson_plan, segment, frame_number)
    prompt = fill_prompt(
        MANIM_RENDER_PROMPT,
        {
            "QUESTION": prompt_safe_text(question, 240),
            "LESSON_JSON": prompt_json(lesson_plan_prompt_view(lesson_plan)),
            "SEGMENT_JSON": prompt_json(segment_prompt_view(segment)),
            "MODE_SELECTION_JSON": prompt_json(selection_prompt_view(selection)),
        },
    )
    raw = await call_model_json(
        prompt,
        FRAME_PLANNER_MODEL,
        fallback,
        operation=f"manim-plan:{segment.get('segment_id') or frame_number}",
        max_tokens=FRAME_MAX_TOKENS,
    )
    plan = normalize_manim_plan(raw, question, lesson_plan, segment, frame_number)
    logger.info(
        "teaching-pipeline manim-plan segment=%s scene_type=%s payload=%s",
        segment.get("segment_id"),
        plan.get("scene_type"),
        compact_json(plan.get("payload")),
    )
    return plan


async def plan_frame_for_segment(
    question: str,
    lesson_plan: dict[str, Any],
    segment: dict[str, Any],
    frame_number: int,
    *,
    segment_plan: dict[str, Any] | None = None,
    storyboard: dict[str, Any] | None = None,
    preferred_visualization: str = "",
) -> dict[str, Any]:
    fallback = heuristic_frame_plan(question, lesson_plan, segment, frame_number)
    if clean_spaces(VISUAL_PIPELINE).lower() != "legacy":
        try:
            frame_plan = build_storyboard_frame_plan(
                question=question,
                lesson_plan=lesson_plan,
                segment_plan=segment_plan or {"segments": []},
                segment=segment,
                frame_number=frame_number,
                storyboard=storyboard or {},
                preferred_visualization=preferred_visualization,
            )
            logger.info("teaching-pipeline storyboard-frame segment=%s data=%s", segment.get("segment_id"), compact_json(frame_plan))
            return frame_plan
        except Exception as exc:
            logger.exception("teaching-pipeline unified visual fallback segment=%s frame=%s error=%s", segment.get("segment_id"), frame_number, exc)
            return fallback
    if isinstance(storyboard, dict) and isinstance(storyboard.get("scene_sequence"), list) and storyboard.get("scene_sequence"):
        frame_plan = build_storyboard_frame_plan(
            question=question,
            lesson_plan=lesson_plan,
            segment_plan=segment_plan or {"segments": []},
            segment=segment,
            frame_number=frame_number,
            storyboard=storyboard,
            preferred_visualization=preferred_visualization,
        )
        logger.info("teaching-pipeline storyboard-frame segment=%s data=%s", segment.get("segment_id"), compact_json(frame_plan))
        return frame_plan

    selection = await select_render_mode_for_segment(
        question,
        lesson_plan,
        segment,
        frame_number,
        preferred_visualization=preferred_visualization,
    )
    excalidraw_fallback = heuristic_excalidraw_plan(question, lesson_plan, segment, frame_number)
    manim_fallback = heuristic_manim_plan(question, lesson_plan, segment, frame_number)

    if selection.get("render_mode") == "manim":
        manim_plan = await plan_manim_renderer_for_segment(question, lesson_plan, segment, frame_number, selection)
        frame_plan = build_frame_plan(
            selection=selection,
            lesson_plan=lesson_plan,
            segment=segment,
            frame_number=frame_number,
            renderer_payload=manim_plan.get("payload") or manim_fallback["payload"],
            layout_notes=manim_plan.get("timing_notes") or "Use motion to teach the visual change clearly.",
            object_actions=manim_plan.get("animation_focus") or manim_fallback["animation_focus"],
            visual_assets=[],
            selected_asset_ids=[],
            fallback_payload=excalidraw_fallback["payload"],
            fallback_reason="Use the Excalidraw-style asset scene if Manim rendering fails.",
        )
    else:
        excalidraw_plan = await plan_excalidraw_renderer_for_segment(question, lesson_plan, segment, frame_number, selection)
        frame_plan = build_frame_plan(
            selection=selection,
            lesson_plan=lesson_plan,
            segment=segment,
            frame_number=frame_number,
            renderer_payload=excalidraw_plan.get("payload") or excalidraw_fallback["payload"],
            layout_notes=excalidraw_plan.get("layout_notes") or excalidraw_fallback["layout_notes"],
            object_actions=excalidraw_plan.get("actions") or excalidraw_fallback["actions"],
            visual_assets=(excalidraw_plan.get("payload") or {}).get("assets", []),
            selected_asset_ids=excalidraw_plan.get("selected_asset_ids") or excalidraw_fallback["selected_asset_ids"],
            fallback_payload=manim_fallback["payload"],
            fallback_reason="Use Manim if the static scene cannot be produced cleanly.",
        )

    if not isinstance(frame_plan, dict):
        frame_plan = fallback
    logger.info(
        "teaching-pipeline visual-path segment=%s frame=%s path=%s",
        segment.get("segment_id"),
        frame_number,
        f"legacy_{normalize_render_mode_name(frame_plan.get('render_mode')) or 'excalidraw'}",
    )
    logger.info("teaching-pipeline frame-plan segment=%s data=%s", segment.get("segment_id"), compact_json(frame_plan))
    return frame_plan


async def run_teaching_pipeline(
    question: str,
    context: str,
    title: str,
    conversation_history: list[dict[str, str]] | None = None,
    fallback_answer: str = "",
    fallback_follow_up: str = "",
    fallback_suggestions: list[str] | None = None,
    *,
    learner_request: str | None = None,
    topic_question: str | None = None,
    context_mode: str = "video_context",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blueprint = await prepare_teaching_blueprint(
        question=question,
        context=context,
        title=title,
        conversation_history=conversation_history,
        fallback_answer=fallback_answer,
        fallback_follow_up=fallback_follow_up,
        fallback_suggestions=fallback_suggestions,
        learner_request=learner_request,
        topic_question=topic_question,
        context_mode=context_mode,
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
        preferred_visualization=preferred_visualization,
        session_state=session_state,
    )
    return await materialize_teaching_blueprint(question, blueprint)


async def stream_teaching_blueprint(
    question: str,
    context: str,
    title: str,
    conversation_history: list[dict[str, str]] | None = None,
    fallback_answer: str = "",
    fallback_follow_up: str = "",
    fallback_suggestions: list[str] | None = None,
    *,
    learner_request: str | None = None,
    topic_question: str | None = None,
    context_mode: str = "video_context",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
):
    pipeline = await build_question_pipeline(
        question=topic_question or question,
        context=context,
        title=title,
        learner_request=learner_request or question,
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
        preferred_visualization=normalize_visualization_preference(preferred_visualization),
        session_state=session_state,
    )
    answer = clean_spaces(pipeline.get("answer")) or clean_spaces(fallback_answer) or clean_spaces(question)
    segments = [item for item in (pipeline.get("teaching_segments") or []) if isinstance(item, dict)]
    first_segment_text = clean_spaces((segments[0] if segments else {}).get("speech_text"))
    yield {
        "event": "first_text",
        "data": {
            "answer": answer,
            "first_segment_text": first_segment_text,
        },
    }
    yield {
        "event": "blueprint",
        "data": {
            **pipeline,
            "answer": answer,
            "follow_up": clean_spaces(pipeline.get("follow_up")) or clean_spaces(fallback_follow_up) or "What should I expand next?",
            "suggestions": [clean_spaces(item) for item in (pipeline.get("suggestions") or fallback_suggestions or []) if clean_spaces(item)][:4],
            "storyboard": pipeline.get("storyboard") or {},
            "preferred_visualization": normalize_visualization_preference(preferred_visualization),
        },
    }


async def prepare_teaching_blueprint(
    question: str,
    context: str,
    title: str,
    conversation_history: list[dict[str, str]] | None = None,
    fallback_answer: str = "",
    fallback_follow_up: str = "",
    fallback_suggestions: list[str] | None = None,
    *,
    learner_request: str | None = None,
    topic_question: str | None = None,
    context_mode: str = "video_context",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blueprint: dict[str, Any] | None = None
    async for event in stream_teaching_blueprint(
        question=question,
        context=context,
        title=title,
        conversation_history=conversation_history,
        fallback_answer=fallback_answer,
        fallback_follow_up=fallback_follow_up,
        fallback_suggestions=fallback_suggestions,
        learner_request=learner_request,
        topic_question=topic_question,
        context_mode=context_mode,
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
        preferred_visualization=preferred_visualization,
        session_state=session_state,
    ):
        if event.get("event") == "blueprint" and isinstance(event.get("data"), dict):
            blueprint = event["data"]
    return blueprint or {
        "answer": clean_spaces(fallback_answer),
        "follow_up": clean_spaces(fallback_follow_up) or "What should I expand next?",
        "suggestions": [clean_spaces(item) for item in (fallback_suggestions or []) if clean_spaces(item)][:4],
        "lesson_plan": {},
        "segment_plan": {"segments": []},
        "storyboard": {},
        "teaching_segments": [],
        "frame_sequence": [],
        "visual_payload": {"segments": []},
        "preferred_visualization": normalize_visualization_preference(preferred_visualization),
    }


async def materialize_frame_plan(frame_plan: dict[str, Any]) -> dict[str, Any]:
    frames = await materialize_frame_sequence([frame_plan] if isinstance(frame_plan, dict) else [])
    return frames[0] if frames else (frame_plan or {})


def build_visual_segment_for_frame(segment_plan: dict[str, Any], frame_plan: dict[str, Any], frame_number: int) -> dict[str, Any] | None:
    segments = segment_plan.get("segments") or []
    if not segments or not isinstance(frame_plan, dict):
        return None
    if frame_number < 1 or frame_number > len(segments):
        return None
    total_duration = sum(
        max(4.0, safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0))
        for segment in segments
    )
    cursor = 0.0
    for index, segment in enumerate(segments, start=1):
        duration = max(4.0, safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0))
        start_pct = cursor / total_duration if total_duration else 0.0
        cursor += duration
        end_pct = cursor / total_duration if total_duration else 1.0
        if index == frame_number:
            return frame_plan_to_visual_segment(frame_plan, start_pct, end_pct)
    return None


async def materialize_teaching_blueprint(question: str, blueprint: dict[str, Any]) -> dict[str, Any]:
    lesson_plan = blueprint.get("lesson_plan") if isinstance(blueprint.get("lesson_plan"), dict) else {}
    segment_plan = blueprint.get("segment_plan") if isinstance(blueprint.get("segment_plan"), dict) else {}
    storyboard = blueprint.get("storyboard") if isinstance(blueprint.get("storyboard"), dict) else {}
    preferred_visualization = normalize_visualization_preference(blueprint.get("preferred_visualization"))
    segments = [item for item in (blueprint.get("teaching_segments") or []) if isinstance(item, dict)]
    preplanned_frames = [item for item in (blueprint.get("frame_sequence") or []) if isinstance(item, dict)]
    if preplanned_frames:
        frame_sequence = preplanned_frames
    else:
        frame_tasks = [
            plan_frame_for_segment(
                question=question,
                lesson_plan=lesson_plan,
                segment=segment,
                frame_number=index,
                segment_plan=segment_plan,
                storyboard=storyboard,
                preferred_visualization=preferred_visualization,
            )
            for index, segment in enumerate(segments, start=1)
        ]
        frame_sequence = await asyncio.gather(*frame_tasks) if frame_tasks else []
    frame_sequence = await materialize_frame_sequence(frame_sequence)
    if preplanned_frames:
        visual_payload = build_synced_visual_payload(frame_sequence)
    else:
        visual_payload = build_visual_payload(segment_plan, frame_sequence)
    return {
        **blueprint,
        "lesson_plan": lesson_plan,
        "segment_plan": segment_plan,
        "teaching_segments": segments,
        "frame_sequence": frame_sequence,
        "visual_payload": visual_payload,
    }


def build_pipeline_board_actions(pipeline_result: dict[str, Any]) -> list[dict[str, str]]:
    lesson_plan = pipeline_result.get("lesson_plan") if isinstance(pipeline_result.get("lesson_plan"), dict) else {}
    segments = pipeline_result.get("teaching_segments") or []
    actions: list[dict[str, str]] = [{"type": "clear"}]
    title = trim_sentence(lesson_plan.get("topic") or "Teaching flow", 48)
    actions.append({"type": "title", "text": title})
    for segment in segments[:3]:
        line = clean_spaces(segment.get("frame_goal") or segment.get("speech_text") or segment.get("label"))
        if line:
            actions.append({"type": "bullet", "text": sentence_case(trim_sentence(line, 120))})
    highlight = clean_spaces(lesson_plan.get("answer_summary")) or clean_spaces((segments[-1] if segments else {}).get("speech_text"))
    if highlight:
        actions.append({"type": "highlight", "text": sentence_case(trim_sentence(highlight, 140))})
    return actions
