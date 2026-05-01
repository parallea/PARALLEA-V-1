from __future__ import annotations

import json
import logging
from typing import Any

from config import GEMINI_API_KEY
from gemini_service import build_gemini_client, generate_json_with_retry
from model_routing import resolve_gemini_model_config

from .prompts.gemini_storyboard_prompt import GEMINI_STORYBOARD_SYSTEM_PROMPT, build_storyboard_prompt
from .storyboard_schema import ScenePlan, Storyboard, storyboard_response_schema
from .storyboard_validator import validate_storyboard
from .utils.animation_patterns import pattern_steps, recommended_patterns_for_scene
from .utils.layout_variation import vary_scene_composition
from .utils.scene_timing import estimate_scene_duration, normalize_requested_depth, pacing_style_for_depth, target_scene_count
from .visual_strategy import (
    SubjectStrategy,
    get_scene_types_for_concept,
    get_strategy_for_subject,
    infer_subject as infer_subject_from_text,
    recommended_motion_patterns,
    should_use_equations_early,
)


logger = logging.getLogger("parallea.visuals")
gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=bool(GEMINI_API_KEY))
KNOWN_SUBJECTS = {"math", "physics", "biology", "chemistry", "cs", "generic"}
STORYBOARD_MODEL_CONFIG = resolve_gemini_model_config(
    "PARALLEA_GEMINI_STORYBOARD_MODEL",
    fallback_envs=["PARALLEA_GEMINI_THINKING_MODEL", "PARALLEA_GEMINI_FRAME_MODEL", "PARALLEA_GEMINI_TEACHING_MODEL"],
    default="gemini-2.5-pro",
    label="storyboard-planner",
)
STORYBOARD_MODEL = STORYBOARD_MODEL_CONFIG["model"]


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def trim_sentence(text: Any, limit: int = 160) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def _clean_json(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    text = clean_spaces(raw)
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception:
        return fallback


def infer_subject(user_question: str, topic: str = "", teaching_answer: str = "") -> str:
    return infer_subject_from_text(user_question, topic, teaching_answer)


def choose_visual_strategy(subject: str, concept_text: str = "", requested_depth: str = "normal") -> SubjectStrategy:
    return get_strategy_for_subject(subject, concept_text, requested_depth)


def _segment_goals(segment_plan: dict[str, Any] | None) -> list[str]:
    goals: list[str] = []
    for item in (segment_plan or {}).get("segments") or []:
        if not isinstance(item, dict):
            continue
        goal = clean_spaces(item.get("frame_goal") or item.get("label"))
        if goal:
            goals.append(goal)
    return goals[:6]


def _answer_sentences(text: str) -> list[str]:
    raw = clean_spaces(text)
    if not raw:
        return []
    parts = [clean_spaces(item) for item in raw.replace("?", ".").replace("!", ".").split(".") if clean_spaces(item)]
    return parts[:8]


def _role_sequence(requested_depth: str, scene_count: int) -> list[str]:
    depth = normalize_requested_depth(requested_depth)
    if depth == "brief":
        base = ["intuition", "mechanism", "formalize"]
    elif depth == "detailed":
        base = ["intuition", "mechanism", "comparison", "formalize", "application", "transfer"]
    else:
        base = ["intuition", "mechanism", "comparison", "formalize", "application"]
    return base[:scene_count]


def _scene_goal_for_role(role: str, concept_summary: str, key_ideas: list[str], scene_type: str) -> str:
    first = key_ideas[0] if key_ideas else concept_summary
    second = key_ideas[1] if len(key_ideas) > 1 else concept_summary
    if role == "intuition":
        return f"Make the main idea immediately visible through {scene_type.replace('_', ' ')} rather than explanation text."
    if role == "mechanism":
        return f"Show how {first.lower()} changes, unfolds, or causes the next state."
    if role == "comparison":
        return f"Contrast {first.lower()} with {second.lower()} so the learner sees what changes and what stays fixed."
    if role == "formalize":
        return "Introduce the formal representation only after the intuition is established."
    if role == "transfer":
        return "Recombine the visual pieces into a reusable mental model."
    return f"Turn {first.lower()} into a concrete visual teaching move."


def _key_visual_objects(subject: str, scene_type: str, concept_summary: str, key_ideas: list[str]) -> list[str]:
    subject_key = clean_spaces(subject).lower()
    concept = clean_spaces(concept_summary)
    if subject_key == "math" and "slope" in concept.lower():
        if scene_type == "graph_intuition":
            return ["slanted line", "moving point", "rise bracket", "run bracket"]
        if scene_type == "rise_run_compare":
            return ["two right triangles on the same line", "equal steepness cue", "rise/run labels"]
        return ["line equation card", "graph anchor", "delta labels"]
    if subject_key == "physics" and "projectile" in concept.lower():
        if scene_type == "motion_arc":
            return ["launched object", "curved path", "peak marker", "landing point"]
        if scene_type == "vector_decomposition":
            return ["velocity vector", "horizontal component", "vertical component", "gravity arrow"]
        return ["trajectory graph", "time axis", "height axis"]
    if subject_key == "cs" and "bfs" in concept.lower():
        if scene_type == "graph_traversal":
            return ["graph nodes", "start node", "frontier highlight", "visited color coding"]
        if scene_type == "queue_frontier":
            return ["queue boxes", "frontier nodes", "next neighbors"]
        return ["level bands", "visited order", "wavefront"]
    if subject_key == "biology":
        return ["system diagram", "flow arrows", "highlighted cycle node"]
    if subject_key == "chemistry":
        return ["particle cluster", "before/after structures", "reaction arrow"]
    base = key_ideas[:2] or [concept_summary]
    return [item for item in base] + ["one transforming visual anchor"]


def fallback_storyboard(
    *,
    user_question: str,
    teaching_answer: str,
    subject: str,
    requested_depth: str,
    user_context: str = "",
    preferred_style: str = "",
    persona_context: str = "",
    lesson_plan: dict[str, Any] | None = None,
    segment_plan: dict[str, Any] | None = None,
) -> Storyboard:
    del user_context
    depth = normalize_requested_depth(requested_depth)
    lesson_plan = lesson_plan or {}
    key_ideas = [clean_spaces(item) for item in (lesson_plan.get("key_ideas") or []) if clean_spaces(item)] or _answer_sentences(teaching_answer)
    concept_summary = trim_sentence(lesson_plan.get("answer_summary") or teaching_answer or user_question, 220)
    teaching_goal = trim_sentence(lesson_plan.get("teaching_objective") or user_question, 180)
    strategy = choose_visual_strategy(subject, f"{user_question} {teaching_answer}", depth)
    scene_types = get_scene_types_for_concept(subject, f"{user_question} {teaching_answer}", depth)
    scene_count = target_scene_count(depth, len((segment_plan or {}).get("segments") or []))
    roles = _role_sequence(depth, scene_count)
    recent_layouts: list[str] = []
    scenes: list[ScenePlan] = []
    motion_preferences = recommended_motion_patterns(subject, f"{user_question} {teaching_answer}")
    segment_ids = [clean_spaces(item.get("segment_id")) for item in ((segment_plan or {}).get("segments") or []) if isinstance(item, dict)]

    for index in range(scene_count):
        scene_type = scene_types[min(index, len(scene_types) - 1)]
        role = roles[min(index, len(roles) - 1)]
        layout = vary_scene_composition(scene_type, subject, index, scene_count, recent_layouts)
        recent_layouts.append(layout)
        if len(recent_layouts) > 3:
            recent_layouts.pop(0)
        pattern_name = motion_preferences[min(index, len(motion_preferences) - 1)] if motion_preferences else recommended_patterns_for_scene(scene_type, subject)[0]
        objects = _key_visual_objects(subject, scene_type, concept_summary, key_ideas)
        emphasis = [key_ideas[min(index, len(key_ideas) - 1)]] if key_ideas else [concept_summary]
        animation_flow = pattern_steps(pattern_name, objects, emphasis)
        equations_usage = "none"
        if role in {"formalize", "application"}:
            equations_usage = "progressive build only if it sharpens understanding"
        elif should_use_equations_early(subject, concept_summary):
            equations_usage = "minimal symbolic anchor"
        scenes.append(
            ScenePlan(
                scene_id=f"scene_{index + 1}",
                scene_goal=_scene_goal_for_role(role, concept_summary, key_ideas, scene_type),
                scene_type=scene_type,
                key_visual_objects=objects,
                animation_flow=animation_flow,
                text_usage="minimal labels only",
                equations_usage=equations_usage,
                transitions="Carry one visual anchor into the next scene instead of hard-cutting to new text.",
                emphasis_points=emphasis,
                estimated_duration=estimate_scene_duration(depth, index, scene_count, role),
                layout_hint=layout,
                camera_behavior="steady framing with small focus shifts" if index != scene_count - 1 else "subtle push toward the key detail",
                pedagogical_role=role,
                segment_ref=segment_ids[min(index, len(segment_ids) - 1)] if segment_ids else "",
            )
        )

    storyboard = Storyboard(
        concept_summary=concept_summary,
        teaching_goal=teaching_goal,
        visual_strategy=strategy.visual_strategy,
        pacing_style=pacing_style_for_depth(depth, scene_count),
        scene_sequence=scenes,
        subject=subject,
        requested_depth=depth,
        preferred_style=trim_sentence(
            preferred_style
            or lesson_plan.get("teaching_style")
            or (f"Mirror this instructor style: {persona_context}" if persona_context else ""),
            120,
        ),
    )
    validation = validate_storyboard(
        storyboard,
        teaching_answer=teaching_answer,
        subject=subject,
        requested_depth=depth,
        auto_repair=True,
    )
    return validation.storyboard


async def _call_storyboard_model(*, prompt: str, fallback: Storyboard) -> Storyboard:
    if not gemini_client:
        return fallback
    try:
        raw = await generate_json_with_retry(
            gemini_client,
            model=STORYBOARD_MODEL,
            prompt=prompt,
            system_instruction=GEMINI_STORYBOARD_SYSTEM_PROMPT,
            response_schema=storyboard_response_schema(),
            temperature=0.2,
            max_output_tokens=2200,
            logger=logger,
            operation="visual-storyboard",
        )
        parsed = _clean_json(raw, fallback.to_dict())
        return Storyboard.from_dict(
            parsed,
            subject=fallback.subject,
            requested_depth=fallback.requested_depth,
            preferred_style=fallback.preferred_style,
        )
    except Exception as exc:
        logger.exception("visual-storyboard model call failed model=%s error=%s", STORYBOARD_MODEL, exc)
        return fallback


async def build_visual_storyboard(
    *,
    user_question: str,
    teaching_answer: str,
    subject: str = "",
    requested_depth: str = "normal",
    user_context: str = "",
    preferred_style: str = "",
    persona_context: str = "",
    lesson_plan: dict[str, Any] | None = None,
    segment_plan: dict[str, Any] | None = None,
) -> Storyboard:
    depth = normalize_requested_depth(requested_depth)
    subject_hint = clean_spaces(subject).lower()
    resolved_subject = subject_hint if subject_hint in KNOWN_SUBJECTS else infer_subject(user_question, subject_hint or (lesson_plan or {}).get("topic", ""), teaching_answer)
    strategy = choose_visual_strategy(resolved_subject, f"{user_question} {teaching_answer}", depth)
    fallback = fallback_storyboard(
        user_question=user_question,
        teaching_answer=teaching_answer,
        subject=resolved_subject,
        requested_depth=depth,
        user_context=user_context,
        preferred_style=preferred_style,
        persona_context=persona_context,
        lesson_plan=lesson_plan,
        segment_plan=segment_plan,
    )
    scene_count = target_scene_count(depth, len((segment_plan or {}).get("segments") or []))
    prompt = build_storyboard_prompt(
        user_question=user_question,
        teaching_answer=teaching_answer,
        subject=resolved_subject,
        requested_depth=depth,
        user_context=user_context,
        preferred_style=preferred_style,
        persona_context=persona_context,
        visual_strategy=strategy.visual_strategy,
        target_scene_count=scene_count,
        response_schema=storyboard_response_schema(),
        segment_goals=_segment_goals(segment_plan),
    )
    storyboard = await _call_storyboard_model(prompt=prompt, fallback=fallback)
    validation = validate_storyboard(
        storyboard,
        teaching_answer=teaching_answer,
        subject=resolved_subject,
        requested_depth=depth,
        auto_repair=True,
    )
    if validation.is_valid:
        return validation.storyboard

    strict_prompt = build_storyboard_prompt(
        user_question=user_question,
        teaching_answer=teaching_answer,
        subject=resolved_subject,
        requested_depth=depth,
        user_context=user_context,
        preferred_style=preferred_style,
        persona_context=persona_context,
        visual_strategy=strategy.visual_strategy,
        target_scene_count=scene_count,
        response_schema=storyboard_response_schema(),
        segment_goals=_segment_goals(segment_plan),
        strict_feedback=validation.issues,
    )
    stricter = await _call_storyboard_model(prompt=strict_prompt, fallback=validation.storyboard)
    stricter_validation = validate_storyboard(
        stricter,
        teaching_answer=teaching_answer,
        subject=resolved_subject,
        requested_depth=depth,
        auto_repair=True,
    )
    if stricter_validation.score and stricter_validation.score.overall_score >= (validation.score.overall_score if validation.score else 0):
        return stricter_validation.storyboard
    return validation.storyboard
