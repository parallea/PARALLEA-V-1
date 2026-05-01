from __future__ import annotations

import logging
import os
import re
from typing import Any

from config import GEMINI_API_KEY, GROQ_API_KEY
from gemini_service import build_gemini_client, generate_json_with_retry
from groq_service import build_async_groq_client, chat_completion_json_with_retry
from model_routing import resolve_gemini_model_config, resolve_groq_model_config

from .explanation_prompt_builder import BASE_SYSTEM_PROMPT, build_explanation_prompt
from .schema import explanation_generator_response_schema
from .session_state import normalize_teaching_session_state, repeat_state_available
from .validators import clean_spaces, parse_json_blob, sentence_case, trim_sentence


logger = logging.getLogger("parallea.explanation")
REMOTE_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if GEMINI_API_KEY else "0") == "1"
GEMINI_MODEL = resolve_gemini_model_config(
    "PARALLEA_GEMINI_EXPLANATION_MODEL",
    fallback_envs=["PARALLEA_GEMINI_TEACHING_MODEL"],
    default="gemini-2.5-flash",
    label="explanation-generator",
)["model"]
GROQ_MODEL = resolve_groq_model_config(
    "PARALLEA_GROQ_EXPLANATION_MODEL",
    fallback_envs=["PARALLEA_GROQ_FIRST_ANSWER_MODEL", "PARALLEA_GROQ_TEACHING_MODEL"],
    default="llama-3.3-70b-versatile",
    label="explanation-generator",
)["model"]
gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=REMOTE_ENABLED)
groq_client = build_async_groq_client(GROQ_API_KEY, enabled=REMOTE_ENABLED)


def _extract_formulae(text: str) -> list[str]:
    candidates = []
    for match in re.findall(r"[A-Za-z][A-Za-z0-9_()]*\s*=\s*[^.]+", text):
        candidate = trim_sentence(match, 120)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates[:4]


def _extract_functions(question: str, explanation: str) -> list[dict[str, Any]]:
    raw = f"{question} {explanation}"
    matches = []
    for pattern in [r"(f\([^)]+\)\s*=\s*[^.]+)", r"(y\s*=\s*[^.]+)"]:
        for expression in re.findall(pattern, raw):
            candidate = trim_sentence(expression, 140)
            if candidate and candidate not in matches:
                matches.append(candidate)
    return [
        {
            "label": trim_sentence(expression.split("=")[0], 32) or "Function",
            "expression": expression,
            "shouldShowOnScreen": True,
            "shouldDrawOnGraph": True,
            "graphNotes": "Show the graph only if it improves understanding.",
        }
        for expression in matches[:2]
    ]


def heuristic_explanation_response(
    *,
    intent: dict[str, Any],
    question: str,
    context: str,
    title: str,
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = normalize_teaching_session_state(session_state)
    mode = clean_spaces(intent.get("mode")) or "simple_explain"
    normalized_question = clean_spaces(intent.get("normalizedQuestion") or question or state.get("lastQuestion"))
    context_lines = [sentence_case(trim_sentence(line, 180)) for line in re.split(r"(?<=[.!?])\s+", clean_spaces(context)) if clean_spaces(line)]
    if mode == "repeat_previous" and repeat_state_available(state):
        explanation = state["lastExplanation"]
        formulae = list(state["lastFormulae"])
        functions = list(state["lastFunctions"])
    else:
        anchor = context_lines[0] if context_lines else sentence_case(trim_sentence(normalized_question or question, 180))
        support = context_lines[1] if len(context_lines) > 1 else ""
        if mode == "brief_explain":
            explanation = f"{anchor} {support}".strip() if support else anchor
        elif mode == "visualize":
            explanation = (
                f"Picture it like this. {anchor} "
                f"{support or 'Use one clear visual anchor, then show how the important part changes or connects.'}"
            ).strip()
        elif mode == "repeat_previous":
            previous_question = state["lastQuestion"] or normalized_question
            explanation = (
                "The previous explanation state was unavailable, so I am rebuilding the last known concept. "
                f"{sentence_case(trim_sentence(previous_question or question, 180))}"
            ).strip()
        else:
            explanation = (
                f"Here is the idea. {anchor} "
                f"{support or 'Build the understanding one clear step at a time instead of adding extra jargon.'}"
            ).strip()
        formulae = _extract_formulae(explanation if clean_spaces(explanation) else normalized_question)
        functions = _extract_functions(normalized_question, explanation)
    return {
        "title": trim_sentence(title or normalized_question or state.get("lastQuestion") or "Lesson", 72),
        "explanation": sentence_case(trim_sentence(explanation, 700)),
        "followUp": "What should I explain next?",
        "formulae": formulae[:4],
        "functions": functions[:2],
    }


async def _call_groq_json(prompt: str) -> dict[str, Any] | None:
    if not groq_client:
        return None
    raw = await chat_completion_json_with_retry(
        groq_client,
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": BASE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        logger=logger,
        operation="explanation-generator",
        temperature=0.25,
        max_tokens=900,
        response_format={"type": "json_object"},
    )
    return parse_json_blob(raw)


async def _call_gemini_json(prompt: str) -> dict[str, Any] | None:
    if not gemini_client:
        return None
    raw = await generate_json_with_retry(
        gemini_client,
        model=GEMINI_MODEL,
        prompt=prompt,
        system_instruction=BASE_SYSTEM_PROMPT,
        response_schema=explanation_generator_response_schema(),
        logger=logger,
        operation="explanation-generator",
        temperature=0.3,
        max_output_tokens=900,
    )
    return parse_json_blob(raw)


def _normalize_explanation_response(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    explanation = sentence_case(trim_sentence(raw.get("explanation"), 700))
    if not explanation:
        return fallback
    formulae = [trim_sentence(item, 120) for item in (raw.get("formulae") or []) if clean_spaces(item)][:4]
    functions = [item for item in (raw.get("functions") or []) if isinstance(item, dict)][:2]
    return {
        "title": trim_sentence(raw.get("title") or fallback["title"], 72),
        "explanation": explanation,
        "followUp": sentence_case(trim_sentence(raw.get("followUp") or fallback["followUp"], 140)),
        "formulae": formulae or fallback["formulae"],
        "functions": functions or fallback["functions"],
    }


async def generate_first_pass_explanation(
    *,
    intent: dict[str, Any],
    question: str,
    context: str,
    title: str,
    learner_request: str = "",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = normalize_teaching_session_state(session_state)
    if clean_spaces(intent.get("mode")) == "repeat_previous" and repeat_state_available(state):
        return {
            "title": trim_sentence(title or state.get("lastNormalizedQuestion") or state.get("lastQuestion") or "Lesson", 72),
            "explanation": state["lastExplanation"],
            "followUp": "Do you want the same explanation one more time, or should I expand one part?",
            "formulae": list(state["lastFormulae"]),
            "functions": list(state["lastFunctions"]),
            "reusedPrevious": True,
            "fallbackUsed": False,
        }
    fallback = heuristic_explanation_response(
        intent=intent,
        question=question,
        context=context,
        title=title,
        session_state=state,
    )
    prompt = build_explanation_prompt(
        intent=intent,
        question=question,
        context=context,
        title=title,
        learner_request=learner_request,
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
        session_state=state,
    )
    parsed = None
    try:
        parsed = await _call_groq_json(prompt)
    except Exception as exc:
        logger.exception("explanation-generator groq failed question=%s error=%s", trim_sentence(question, 160), exc)
    if not isinstance(parsed, dict):
        try:
            parsed = await _call_gemini_json(prompt)
        except Exception as exc:
            logger.exception("explanation-generator gemini failed question=%s error=%s", trim_sentence(question, 160), exc)
    normalized = _normalize_explanation_response(parsed or {}, fallback)
    normalized["reusedPrevious"] = False
    normalized["fallbackUsed"] = normalized == fallback
    return normalized
