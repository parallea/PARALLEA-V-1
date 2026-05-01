from __future__ import annotations

import json
from typing import Any


GEMINI_STORYBOARD_SYSTEM_PROMPT = """
You are not summarizing. You are designing an educational animation storyboard.

Think like:
- a visual educator
- an animation director
- a concept-first lesson designer

Hard rules:
- Do not split the answer paragraph into sentences and animate each sentence.
- Do not dump the full answer on screen.
- Do not default to title -> text -> equation -> fade.
- Derive the underlying concept first, then choose the visual path.
- Prefer motion, transformation, comparison, spatial relationships, diagrams, graphs, flow, and metaphor.
- Use text minimally and only when it improves orientation.
- Use equations late unless the concept is inherently symbolic.
- Each scene must have a distinct pedagogical purpose.
- Motion must carry meaning.
- If the explanation is brief, compress the number of scenes, not the visual quality.
- Output valid JSON only.
""".strip()


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_storyboard_prompt(
    *,
    user_question: str,
    teaching_answer: str,
    subject: str,
    requested_depth: str,
    user_context: str,
    preferred_style: str,
    persona_context: str,
    visual_strategy: str,
    target_scene_count: int,
    response_schema: dict[str, Any],
    segment_goals: list[str] | None = None,
    strict_feedback: list[str] | None = None,
) -> str:
    segment_rows = [goal for goal in (segment_goals or []) if goal][:6]
    repair_lines = "\n".join(f"- {item}" for item in (strict_feedback or []) if item)
    return f"""
Design a storyboard for a Manim-based educational animation.

Inputs:
- User question: {user_question}
- Teaching answer: {teaching_answer}
- Subject/topic: {subject}
- Requested depth: {requested_depth}
- Optional user context: {user_context or "None"}
- Preferred style: {preferred_style or "Teacher-authored, concept-first"}
{f"- Instructor persona to mirror: {persona_context}" if persona_context else ""}

Storyboard intent:
- Think visually before rendering.
- Start from conceptual understanding, then choose the visual path.
- Make the visuals teach, not decorate.
- Use distinct scene composition across the storyboard.
- Use equations only when they improve understanding.
- Prefer intuition before formalism.
- Build around this strategy: {visual_strategy}
- Target approximately {target_scene_count} scenes.

Alignment notes:
- These spoken beat goals exist only to keep pacing aligned, not to force line-by-line conversion:
{json.dumps(segment_rows, ensure_ascii=False, indent=2) if segment_rows else '[]'}

Quality constraints:
- More than one scene should involve meaningful transformation, contrast, or motion relationships.
- Avoid repeated layout_hint across the whole storyboard.
- Keep text_usage low enough that the storyboard is not text-heavy.
- Make at least one scene comparison-based or transformation-based.
- If equations appear, introduce them progressively and later.

{f"Previous storyboard problems to fix:\\n{repair_lines}" if repair_lines else ""}

Return JSON only matching this schema:
{_compact_json(response_schema)}
""".strip()
