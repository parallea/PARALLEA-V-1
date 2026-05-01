from __future__ import annotations

from typing import Any

from .validators import clean_spaces, trim_sentence


BASE_SYSTEM_PROMPT = """
You are Parallea's explanation generator inside an immersive teaching flow.
Return valid JSON only. Do not use markdown fences.
Write for spoken delivery.
Keep the explanation correct, teacher-like, and visually segmentable.
Never talk about prompts, retrieval, tools, or missing context.
""".strip()


MODE_GUIDANCE = {
    "simple_explain": """
Simple explain mode:
- explain clearly in simple language
- teach like a patient tutor
- preserve correctness
- avoid unnecessary jargon
- keep a natural teaching flow that can later be segmented into visuals
""".strip(),
    "brief_explain": """
Brief explain mode:
- explain the idea concisely
- keep it compact
- avoid long digressions
- preserve correctness
- use one extra sentence only when correctness needs it
""".strip(),
    "repeat_previous": """
Repeat mode:
- if a previous explanation exists, keep the same core wording and structure
- preserve the same formulas and functions when they matter
- do not invent a new explanation unless prior state is unavailable
- if prior state is unavailable, say that briefly and regenerate the last known concept
""".strip(),
    "visualize": """
Visualize mode:
- make the explanation scene-friendly
- use concrete imagery and a real-life analogy when helpful
- let the spoken answer reference what the learner should picture
- prefer intuitive comparisons, arrows, step-by-step scenes, and on-screen relationships
""".strip(),
}


def build_explanation_prompt(
    *,
    intent: dict[str, Any],
    question: str,
    context: str,
    title: str,
    learner_request: str = "",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> str:
    mode = clean_spaces(intent.get("mode")) or "simple_explain"
    state = session_state or {}
    question_text = trim_sentence(question or intent.get("normalizedQuestion") or intent.get("rawQuestion"), 260)
    learner_text = trim_sentence(learner_request or intent.get("rawQuestion") or question, 260)
    prior_question = trim_sentence(state.get("lastQuestion"), 220)
    prior_explanation = trim_sentence(state.get("lastExplanation"), 520)
    context_block = trim_sentence(context, 1800) or "No extra classroom context was provided."
    persona_block = trim_sentence(persona_context, 240)
    title_text = trim_sentence(title or question_text, 120)
    return f"""
{MODE_GUIDANCE.get(mode, MODE_GUIDANCE["simple_explain"])}

Output shape:
{{
  "title": "short lesson title",
  "explanation": "spoken explanation",
  "followUp": "one short follow-up question",
  "formulae": ["optional formula"],
  "functions": [
    {{
      "label": "optional function label",
      "expression": "optional function expression",
      "shouldShowOnScreen": true,
      "shouldDrawOnGraph": false,
      "graphNotes": "optional graph note"
    }}
  ]
}}

Teaching constraints:
- speak naturally, not like a generic chatbot
- the explanation should align with a future visual sequence
- prefer concrete sentences that can map to timed frames
- if formulas matter, introduce meaning before notation
- if functions matter, make the graphing intent explicit
- if the learner asked briefly, keep the explanation short
- if the learner asked to visualize, mention what should appear on screen

Current title: {title_text}
Pedagogy mode: {clean_spaces(pedagogy_mode) or "simple"}
Learner request: {learner_text}
Normalized topic question: {question_text}
Needs visuals: {bool(intent.get("wantsVisuals"))}
Needs formulae: {bool(intent.get("wantsFormulae"))}
Needs graphing: {bool(intent.get("wantsFunctionGraph"))}
Use a real-life example: {bool(intent.get("useRealLifeExample"))}
Persona guidance: {persona_block or "None"}

Classroom context:
{context_block}

Previous reusable state:
- last question: {prior_question or "None"}
- last explanation: {prior_explanation or "None"}
""".strip()
