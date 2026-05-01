from __future__ import annotations

import logging
import os
from typing import Any

from config import GEMINI_API_KEY
from gemini_service import build_gemini_client, generate_json_with_retry
from manim_renderer import heuristic_manim_payload
from model_routing import resolve_gemini_model_config

from .schema import manim_frame_plan_schema
from .validators import clean_spaces, normalize_manim_frame_plan, parse_json_blob, sentence_case, trim_sentence


logger = logging.getLogger("parallea.manim-adapter")
REMOTE_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if GEMINI_API_KEY else "0") == "1"
MANIM_ADAPTER_MODEL = resolve_gemini_model_config(
    "PARALLEA_GEMINI_MANIM_ADAPTER_MODEL",
    fallback_envs=["PARALLEA_GEMINI_FRAME_MODEL", "PARALLEA_GEMINI_TEACHING_MODEL"],
    default="gemini-2.5-flash",
    label="manim-adapter",
)["model"]
gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=REMOTE_ENABLED)


def _heuristic_plan(frame: dict[str, Any]) -> dict[str, Any]:
    objects = []
    for index, formula in enumerate((frame.get("formulae") or [])[:2], start=1):
        objects.append(
            {
                "id": f"obj_formula_{index}",
                "type": "mathtex",
                "content": "",
                "expression": trim_sentence(formula, 120),
                "animation": "Write",
                "notes": "Reveal the formula only when the narration introduces it.",
            }
        )
    if frame.get("functionsToDraw"):
        objects.append(
            {
                "id": "obj_axes",
                "type": "axes",
                "content": "",
                "expression": "",
                "animation": "Create",
                "notes": "Draw clean axes first.",
            }
        )
        for index, function in enumerate(frame.get("functionsToDraw") or [], start=1):
            objects.append(
                {
                    "id": f"obj_plot_{index}",
                    "type": "plot",
                    "content": trim_sentence(function.get("label"), 42),
                    "expression": trim_sentence(function.get("expression"), 140),
                    "animation": "Create",
                    "notes": trim_sentence(function.get("graphNotes"), 140) or "Plot the function clearly.",
                }
            )
    if not objects:
        objects.append(
            {
                "id": "obj_text_1",
                "type": "text",
                "content": trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription"), 140),
                "expression": "",
                "animation": "Write",
                "notes": "Keep the text minimal.",
            }
        )
    sequence = []
    for index, item in enumerate(objects, start=1):
        sequence.append(
            {
                "step": index,
                "action": item.get("animation") or "Show",
                "targetIds": [item["id"]],
                "narrationCue": sentence_case(trim_sentence(frame.get("sceneDescription") or frame.get("visualGoal"), 120)),
            }
        )
    return {
        "frameId": clean_spaces(frame.get("id")) or "frame_1",
        "sceneSummary": sentence_case(trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription"), 180)),
        "objects": objects[:6],
        "sequence": sequence[:6],
    }


def build_manim_adapter_prompt(frame: dict[str, Any], context: str = "") -> str:
    return f"""
You are Parallea's Manim frame adapter.
Return valid JSON only. Do not use markdown fences.

Rules:
- prefer clean educational animation
- no unnecessary cinematic effects
- keep the object list focused on what teaches the concept
- use Manim only for graphing, geometry change, precise equations, or motion that adds understanding

Frame:
{frame}

Context:
{trim_sentence(context, 800) or "No extra context."}
""".strip()


async def build_manim_frame_plan(frame: dict[str, Any], context: str = "") -> dict[str, Any]:
    fallback = _heuristic_plan(frame)
    if not gemini_client:
        return fallback
    try:
        raw = await generate_json_with_retry(
            gemini_client,
            model=MANIM_ADAPTER_MODEL,
            prompt=build_manim_adapter_prompt(frame, context=context),
            system_instruction="Return valid JSON only. Do not use markdown fences.",
            response_schema=manim_frame_plan_schema(),
            logger=logger,
            operation=f"manim-adapter:{clean_spaces(frame.get('id')) or 'frame'}",
            temperature=0.2,
            max_output_tokens=1000,
        )
        return normalize_manim_frame_plan(parse_json_blob(raw), fallback=fallback)
    except Exception as exc:
        logger.exception("manim-adapter failed frame=%s error=%s", frame.get("id"), exc)
        return fallback


def manim_plan_to_renderer_payload(plan: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any]:
    lesson_plan = {
        "topic": trim_sentence(plan.get("sceneSummary") or frame.get("sceneDescription"), 72),
        "key_ideas": [trim_sentence(frame.get("visualGoal"), 140)],
        "key_formulas": [{"formula": item.get("expression"), "meaning": item.get("notes"), "when_to_use": item.get("animation")} for item in plan.get("objects", []) if clean_spaces(item.get("expression"))],
        "teaching_steps": [
            {
                "step_id": "step_1",
                "label": trim_sentence(frame.get("sceneDescription"), 48),
                "key_idea": trim_sentence(frame.get("visualGoal"), 160),
                "explanation": trim_sentence(frame.get("sceneDescription"), 220),
                "visual_focus": trim_sentence(frame.get("visualGoal"), 180),
                "formula": trim_sentence((frame.get("formulae") or [""])[0], 120),
                "formula_terms": [],
            }
        ],
    }
    segment = {
        "segment_id": clean_spaces(frame.get("id")) or "frame_1",
        "label": trim_sentence(frame.get("sceneDescription"), 48),
        "speech_text": sentence_case(trim_sentence(frame.get("sceneDescription"), 220)),
        "frame_goal": sentence_case(trim_sentence(frame.get("visualGoal"), 180)),
        "timing_hint": {"target_duration_sec": max(4, len(plan.get("sequence") or []) * 2), "pace": "medium"},
    }
    payload = heuristic_manim_payload(frame.get("sceneDescription") or frame.get("visualGoal"), lesson_plan, segment)
    payload["adapterPlan"] = plan
    payload["subtitle"] = trim_sentence(frame.get("visualGoal") or payload.get("subtitle"), 92)
    return payload
