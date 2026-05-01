from __future__ import annotations

import logging
import os
import re
from typing import Any

from board_asset_library import suggest_excalidraw_assets
from board_scene_library import suggest_scene_objects
from config import GEMINI_API_KEY
from gemini_service import build_gemini_client, generate_json_with_retry
from model_routing import resolve_gemini_model_config

from .available_excalidraw_elements import excalidraw_element_ids, excalidraw_elements_library_text
from .schema import gemini_scene_output_schema
from .session_state import normalize_teaching_session_state, repeat_state_available
from .validators import (
    clean_spaces,
    estimate_duration_seconds,
    format_timecode,
    normalize_gemini_scene_output,
    parse_json_blob,
    parse_timecode,
    sentence_case,
    trim_sentence,
)


logger = logging.getLogger("parallea.scene-director")
REMOTE_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if GEMINI_API_KEY else "0") == "1"
SCENE_DIRECTOR_MODEL = resolve_gemini_model_config(
    "PARALLEA_GEMINI_SCENE_DIRECTOR_MODEL",
    fallback_envs=["PARALLEA_GEMINI_TEACHING_MODEL", "PARALLEA_GEMINI_SEGMENT_MODEL"],
    default="gemini-2.5-flash",
    label="scene-director",
)["model"]
gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=REMOTE_ENABLED)


def _split_explanation(explanation: str, *, brief: bool = False) -> list[str]:
    parts = [clean_spaces(part) for part in re.split(r"(?<=[.!?])\s+", clean_spaces(explanation)) if clean_spaces(part)]
    if not parts and clean_spaces(explanation):
        parts = [sentence_case(explanation)]
    if brief and len(parts) > 2:
        return parts[:2]
    return parts[:4] or ["Explain the core idea."]


def _infer_formulae(question: str, explanation_package: dict[str, Any]) -> list[str]:
    formulae = [trim_sentence(item, 120) for item in (explanation_package.get("formulae") or []) if clean_spaces(item)]
    if formulae:
        return formulae[:4]
    combined = f"{question} {explanation_package.get('explanation', '')}"
    matches = re.findall(r"[A-Za-z][A-Za-z0-9_()]*\s*=\s*[^.]+", combined)
    deduped = []
    for item in matches:
        candidate = trim_sentence(item, 120)
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped[:4]


def _infer_functions(question: str, explanation_package: dict[str, Any]) -> list[dict[str, Any]]:
    functions = [item for item in (explanation_package.get("functions") or []) if isinstance(item, dict)]
    if functions:
        return functions[:3]
    combined = f"{question} {explanation_package.get('explanation', '')}"
    matches = []
    for pattern in [r"(f\([^)]+\)\s*=\s*[^.]+)", r"(y\s*=\s*[^.]+)"]:
        for expression in re.findall(pattern, combined):
            cleaned = trim_sentence(expression, 140)
            if cleaned and cleaned not in matches:
                matches.append(cleaned)
    return [
        {
            "label": trim_sentence(expression.split("=")[0], 42) or "Function",
            "expression": expression,
            "shouldShowOnScreen": True,
            "shouldDrawOnGraph": True,
            "graphNotes": "Draw the function on axes while it is explained.",
        }
        for expression in matches[:2]
    ]


def _should_use_manim(text: str, *, function_graph: bool = False, formula_count: int = 0) -> bool:
    lowered = clean_spaces(text).lower()
    if function_graph:
        return True
    if formula_count >= 2 and any(term in lowered for term in ["solve", "derive", "transform", "equation"]):
        return True
    return any(
        term in lowered
        for term in [
            "graph",
            "plot",
            "curve",
            "function",
            "slope",
            "geometry",
            "triangle",
            "rotate",
            "transform",
            "translate",
            "reflect",
            "equation",
            "axes",
        ]
    )


def _suggest_elements(question: str, sentence: str) -> list[str]:
    assets = [f"asset:{name}" for name in suggest_excalidraw_assets(question, sentence, limit=2)]
    semantic = [f"semantic:{name}" for name in suggest_scene_objects(question, sentence, sentence, limit=2)]
    combined: list[str] = []
    for item in assets + semantic:
        if item not in combined:
            combined.append(item)
    return combined[:3]


def fallback_scene_output(
    *,
    intent: dict[str, Any],
    question: str,
    explanation_package: dict[str, Any],
) -> dict[str, Any]:
    explanation = clean_spaces(explanation_package.get("explanation")) or sentence_case(question)
    segments_text = _split_explanation(explanation, brief=clean_spaces(intent.get("mode")) == "brief_explain")
    formulae = _infer_formulae(question, explanation_package)
    functions = _infer_functions(question, explanation_package)
    spoken_segments = []
    frames = []
    cursor = 0
    for index, sentence in enumerate(segments_text, start=1):
        duration = estimate_duration_seconds(sentence, minimum=4, maximum=8 if clean_spaces(intent.get("mode")) == "brief_explain" else 10)
        start = format_timecode(cursor)
        cursor += duration
        end = format_timecode(cursor)
        purpose = "intro" if index == 1 else "summary" if index == len(segments_text) else "core_explanation"
        if index == len(segments_text) and clean_spaces(intent.get("mode")) == "visualize":
            purpose = "example"
        if any(item in sentence.lower() for item in ["formula", "equation", "="]):
            purpose = "formula"
        spoken_segments.append({"id": f"segment_{index}", "start": start, "end": end, "text": sentence_case(sentence), "purpose": purpose})
        should_graph = bool(functions and (intent.get("wantsFunctionGraph") or "graph" in sentence.lower() or "plot" in sentence.lower()))
        visualizer = "manim" if _should_use_manim(sentence, function_graph=should_graph, formula_count=len(formulae)) else "excalidraw"
        if clean_spaces(intent.get("mode")) == "visualize" and not should_graph:
            visualizer = "excalidraw"
        frames.append(
            {
                "id": f"frame_{index}",
                "sceneDescription": sentence_case(sentence),
                "timelineStart": start,
                "timelineEnd": end,
                "formulae": formulae[:2] if purpose == "formula" or (index == len(segments_text) and formulae) else [],
                "functionsToShow": functions[:2] if should_graph else [],
                "functionsToDraw": functions[:2] if should_graph else [],
                "visualizer": visualizer,
                "visualGoal": sentence_case(sentence if visualizer == "manim" else f"Show the main relationship behind: {sentence}"),
                "visualNotes": [
                    sentence_case("Keep the visual tightly aligned to the spoken sentence."),
                    sentence_case("Use minimal relevant elements only."),
                ],
                "analogy": sentence_case("Use a real-life comparison if it makes the concept more intuitive.") if intent.get("useRealLifeExample") else "",
                "elementsNeeded": _suggest_elements(question, sentence) if visualizer == "excalidraw" else [],
            }
        )
    return {
        "answerMode": clean_spaces(intent.get("mode")) or "simple_explain",
        "spokenAnswerSegments": spoken_segments,
        "formulae": formulae,
        "functions": functions,
        "frames": frames,
    }


def build_scene_director_prompt(
    *,
    intent: dict[str, Any],
    question: str,
    explanation_package: dict[str, Any],
    context: str,
    title: str,
) -> str:
    explanation = trim_sentence(explanation_package.get("explanation"), 1800)
    formulae = [trim_sentence(item, 120) for item in (explanation_package.get("formulae") or []) if clean_spaces(item)]
    functions = [item for item in (explanation_package.get("functions") or []) if isinstance(item, dict)]
    return f"""
You are Parallea's Gemini scene director.
Return valid JSON only. Do not use markdown fences.

Your job:
1. segment the spoken explanation into timed spoken parts
2. decide formulas to show
3. decide functions to show or draw
4. create frame-by-frame scene plans
5. choose the best visualizer for each frame

Product rules:
- Default to `excalidraw` for conceptual teaching, process diagrams, biology, general education, and lesson-board visuals.
- Use `manim` only for math-heavy animation, function graphing, geometry transformations, or precise equation animation.
- Never invent Excalidraw elements. Use only ids from the provided library.
- Keep visuals minimal and directly relevant.
- The spoken narration must line up with what appears on screen.
- If formulas or graphs appear, mention them while they are on screen.

Question: {trim_sentence(question, 260)}
Mode: {clean_spaces(intent.get("mode"))}
Normalized question: {trim_sentence(intent.get("normalizedQuestion"), 220)}
Title: {trim_sentence(title, 120)}
Needs visuals: {bool(intent.get("wantsVisuals"))}
Needs formulae: {bool(intent.get("wantsFormulae"))}
Needs function graph: {bool(intent.get("wantsFunctionGraph"))}
Use a real-life example: {bool(intent.get("useRealLifeExample"))}

Explanation package:
- explanation: {explanation}
- formulae hints: {formulae}
- function hints: {functions}
- follow up: {trim_sentence(explanation_package.get("followUp"), 140)}

Classroom context:
{trim_sentence(context, 1200) or "No extra classroom context."}

Available Excalidraw-compatible elements:
{excalidraw_elements_library_text()}

Output contract:
- answerMode must be one of: simple_explain, brief_explain, repeat_previous, visualize
- spokenAnswerSegments use HH:MM:SS start/end values
- frames must align with spokenAnswerSegments
- elementsNeeded is only for excalidraw frames and must use provided ids
""".strip()


async def direct_scenes(
    *,
    intent: dict[str, Any],
    question: str,
    explanation_package: dict[str, Any],
    context: str,
    title: str,
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = normalize_teaching_session_state(session_state)
    if clean_spaces(intent.get("mode")) == "repeat_previous" and repeat_state_available(state) and isinstance(state.get("lastSceneOutput"), dict):
        return {
            **state["lastSceneOutput"],
            "reusedPrevious": True,
            "fallbackUsed": False,
        }
    fallback = fallback_scene_output(intent=intent, question=question, explanation_package=explanation_package)
    prompt = build_scene_director_prompt(
        intent=intent,
        question=question,
        explanation_package=explanation_package,
        context=context,
        title=title,
    )
    if not gemini_client:
        return {**fallback, "reusedPrevious": False, "fallbackUsed": True}
    try:
        raw = await generate_json_with_retry(
            gemini_client,
            model=SCENE_DIRECTOR_MODEL,
            prompt=prompt,
            system_instruction="Return valid JSON only. Do not use markdown fences.",
            response_schema=gemini_scene_output_schema(),
            logger=logger,
            operation="scene-director",
            temperature=0.25,
            max_output_tokens=1800,
        )
        parsed = parse_json_blob(raw)
        normalized = normalize_gemini_scene_output(
            parsed,
            fallback=fallback,
            forced_mode=clean_spaces(intent.get("mode")),
            allowed_excalidraw_elements=excalidraw_element_ids(),
        )
        normalized["reusedPrevious"] = False
        normalized["fallbackUsed"] = normalized == fallback
        return normalized
    except Exception as exc:
        logger.exception("scene-director failed question=%s error=%s", trim_sentence(question, 160), exc)
        return {**fallback, "reusedPrevious": False, "fallbackUsed": True}
