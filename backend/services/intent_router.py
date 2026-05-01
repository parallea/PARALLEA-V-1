from __future__ import annotations

import re
from typing import Any

from .validators import clean_spaces


REPEAT_PATTERNS = [
    r"\bsay (?:that|it) again\b",
    r"\brepeat (?:that|it|this)?\b",
    r"\bgo over (?:that|it|this) again\b",
    r"\bcan you please repeat\b",
    r"\bone more time\b",
]
VISUAL_PATTERNS = [
    r"\bvisuali[sz]e\b",
    r"\bshow me visually\b",
    r"\bhelp me visualize\b",
    r"\bdiagram\b",
    r"\billustrate\b",
    r"\bdraw\b",
    r"\bpicture this\b",
]
BRIEF_PATTERNS = [
    r"\bbrief(?:ly)?\b",
    r"\bshort(?:ly)?\b",
    r"\bconcise\b",
    r"\bin short\b",
    r"\bquick(?:ly)?\b",
    r"\btl;dr\b",
]
EXPLAIN_PATTERNS = [
    r"\bexplain\b",
    r"\bwalk me through\b",
    r"\bwhat is\b",
    r"\bhow does\b",
    r"\bhelp me understand\b",
    r"\bcan you explain\b",
]
FORMULAE_PATTERNS = [
    r"\bformula\b",
    r"\bequation\b",
    r"\bderive\b",
    r"\bderivative\b",
    r"\bintegral\b",
    r"\bsolve\b",
    r"\bproof\b",
    r"\balgebra\b",
    r"\bcalculate\b",
    r"\bcompute\b",
    r"\bfunction\b",
]
FUNCTION_GRAPH_PATTERNS = [
    r"\bgraph\b",
    r"\bplot\b",
    r"\bfunction\b",
    r"\bcurve\b",
    r"\bline\b",
    r"\bparabola\b",
    r"\baxes\b",
    r"\bslope\b",
    r"\bf\(",
]
REAL_LIFE_PATTERNS = [
    r"\breal[- ]life\b",
    r"\banalogy\b",
    r"\bintuitive\b",
    r"\bexample\b",
    r"\beveryday\b",
]


FILLER_PHRASES = [
    r"\bcan you\b",
    r"\bcould you\b",
    r"\bwould you\b",
    r"\bplease\b",
    r"\bhelp me\b",
    r"\bshow me\b",
    r"\btell me\b",
    r"\bbriefly\b",
    r"\bquickly\b",
    r"\bjust\b",
    r"\bthis\b",
    r"\bthat\b",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _normalize_question(raw_question: str) -> str:
    question = clean_spaces(raw_question)
    if not question:
        return ""
    normalized = question
    normalized = re.sub(r"^[,\s]+|[?.!,\s]+$", "", normalized)
    normalized = re.sub(r"^(can|could|would)\s+you\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^(please|briefly|quickly)\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^(help me|show me|tell me)\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^(explain|repeat|visualize)\s+(this|that|it)\s*", "", normalized, flags=re.I)
    normalized = re.sub(r"^(briefly\s+)?explain\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^(help me\s+)?visuali[sz]e\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^(show me\s+visually\s+)", "", normalized, flags=re.I)
    normalized = re.sub(r"^(can you please repeat it|say that again|repeat that)\b", "", normalized, flags=re.I)
    normalized = clean_spaces(normalized)
    if not normalized:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9_+\-*/^()=.]+", question)
            if clean_spaces(token).lower() not in {"please", "briefly", "explain", "repeat", "visualize", "again"}
        ]
        normalized = " ".join(tokens)
    normalized = re.sub(r"\s+[?.!,]+$", "", normalized)
    return normalized or clean_spaces(question)


def route_explanation_intent(question: Any) -> dict[str, Any]:
    raw_question = clean_spaces(question)
    lowered = raw_question.lower()
    normalized_question = _normalize_question(raw_question)
    if _matches_any(lowered, REPEAT_PATTERNS):
        mode = "repeat_previous"
    elif _matches_any(lowered, VISUAL_PATTERNS):
        mode = "visualize"
    elif _matches_any(lowered, BRIEF_PATTERNS):
        mode = "brief_explain"
    else:
        mode = "simple_explain"
    wants_repeat = mode == "repeat_previous"
    wants_function_graph = _matches_any(lowered, FUNCTION_GRAPH_PATTERNS)
    wants_formulae = wants_function_graph or _matches_any(lowered, FORMULAE_PATTERNS) or bool(re.search(r"[=^+\-/*()]", raw_question))
    wants_visuals = mode == "visualize" or wants_function_graph or any(term in lowered for term in ["diagram", "draw", "show", "visual", "graph"])
    use_real_life_example = mode == "visualize" or _matches_any(lowered, REAL_LIFE_PATTERNS)
    return {
        "rawQuestion": raw_question,
        "normalizedQuestion": normalized_question,
        "mode": mode,
        "wantsVisuals": wants_visuals,
        "wantsRepeat": wants_repeat,
        "wantsFormulae": wants_formulae,
        "wantsFunctionGraph": wants_function_graph,
        "useRealLifeExample": use_real_life_example,
    }
