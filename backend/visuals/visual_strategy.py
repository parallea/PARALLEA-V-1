from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


@dataclass(frozen=True, slots=True)
class SubjectStrategy:
    subject: str
    visual_strategy: str
    builder_key: str
    preferred_scene_types: tuple[str, ...]
    motion_patterns: tuple[str, ...]
    equation_policy: str
    layout_cycle: tuple[str, ...]


STRATEGY_LIBRARY: dict[str, SubjectStrategy] = {
    "math": SubjectStrategy(
        subject="math",
        visual_strategy="symbolic transformation grounded in graph, geometry, and contrast-based intuition",
        builder_key="math",
        preferred_scene_types=("graph_intuition", "rise_run_compare", "geometry_construction", "symbolic_formalize"),
        motion_patterns=("trace_then_label", "compare_and_merge", "graph_to_equation", "progressive_equation_build"),
        equation_policy="late_unless_symbolic",
        layout_cycle=("center_morph", "compare_before_after", "left_visual_right_labels", "zoom_into_detail"),
    ),
    "physics": SubjectStrategy(
        subject="physics",
        visual_strategy="show forces and motion as relationships over time before formal equations",
        builder_key="physics",
        preferred_scene_types=("motion_arc", "vector_decomposition", "system_interaction", "graph_connection"),
        motion_patterns=("trace_then_label", "decompose_and_rebuild", "focus_shift", "object_to_symbol"),
        equation_policy="late_after_motion",
        layout_cycle=("center_morph", "top_bottom_causal_flow", "left_visual_right_labels", "compare_before_after"),
    ),
    "biology": SubjectStrategy(
        subject="biology",
        visual_strategy="teach systems, pathways, and cycles through flow, layering, and cause-effect motion",
        builder_key="biology",
        preferred_scene_types=("system_flow", "cycle_flow", "layered_structure", "comparison_transform"),
        motion_patterns=("reveal_then_transform", "focus_shift", "decompose_and_rebuild", "compare_and_merge"),
        equation_policy="rare",
        layout_cycle=("top_bottom_causal_flow", "radial_build", "zoom_into_detail", "center_morph"),
    ),
    "chemistry": SubjectStrategy(
        subject="chemistry",
        visual_strategy="use particles, structure contrast, and reaction flow rather than paragraph labels",
        builder_key="chemistry",
        preferred_scene_types=("particle_motion", "structure_compare", "reaction_flow", "energy_landscape"),
        motion_patterns=("compare_and_merge", "decompose_and_rebuild", "focus_shift", "object_to_symbol"),
        equation_policy="late_after_structure",
        layout_cycle=("center_morph", "compare_before_after", "top_bottom_causal_flow", "zoom_into_detail"),
    ),
    "cs": SubjectStrategy(
        subject="cs",
        visual_strategy="show state, traversal, queue/data flow, and unfolding structure rather than prose",
        builder_key="cs",
        preferred_scene_types=("graph_traversal", "queue_frontier", "state_transition", "data_flow"),
        motion_patterns=("focus_shift", "decompose_and_rebuild", "compare_and_merge", "reveal_then_transform"),
        equation_policy="minimal",
        layout_cycle=("center_morph", "left_visual_right_labels", "top_bottom_causal_flow", "timeline_style"),
    ),
    "generic": SubjectStrategy(
        subject="generic",
        visual_strategy="start with a strong mental model, then contrast, transform, and formalize only when useful",
        builder_key="generic",
        preferred_scene_types=("concept_metaphor", "comparison_transform", "process_reveal", "symbolic_formalize"),
        motion_patterns=("reveal_then_transform", "compare_and_merge", "focus_shift", "progressive_equation_build"),
        equation_policy="late_if_needed",
        layout_cycle=("center_morph", "compare_before_after", "top_bottom_causal_flow", "zoom_into_detail"),
    ),
}


SUBJECT_KEYWORDS = {
    "math": ["slope", "equation", "algebra", "geometry", "triangle", "calculus", "derivative", "integral", "function", "graph", "number line"],
    "physics": ["projectile", "velocity", "acceleration", "gravity", "force", "momentum", "trajectory", "vector", "energy", "field"],
    "biology": ["cell", "blood", "circulate", "circulation", "respiration", "organ", "photosynthesis", "ecosystem", "dna", "enzyme"],
    "chemistry": ["atom", "molecule", "reaction", "bond", "electron", "acid", "base", "equilibrium", "compound", "stoichiometry"],
    "cs": ["algorithm", "graph", "queue", "stack", "bfs", "dfs", "tree", "array", "pointer", "state machine", "recursion", "sort"],
}


def infer_subject(question: str, topic: str = "", answer: str = "") -> str:
    blob = clean_spaces(" ".join([question, topic, answer])).lower()
    if "projectile" in blob or "gravity" in blob or "velocity" in blob:
        return "physics"
    if "blood" in blob or "circulate" in blob or "circulation" in blob:
        return "biology"
    if "bfs" in blob or "queue" in blob or "algorithm" in blob or "graph traversal" in blob:
        return "cs"
    if "molecule" in blob or "reaction" in blob or "atom" in blob:
        return "chemistry"
    if "slope" in blob or "equation" in blob or "calculus" in blob or "triangle" in blob:
        return "math"

    scores: dict[str, int] = {}
    for subject, keywords in SUBJECT_KEYWORDS.items():
        scores[subject] = sum(1 for keyword in keywords if keyword in blob)
    best_subject = max(scores, key=scores.get, default="generic")
    return best_subject if scores.get(best_subject, 0) > 0 else "generic"


def get_strategy_for_subject(subject: str, concept_text: str = "", requested_depth: str = "normal") -> SubjectStrategy:
    del concept_text, requested_depth
    return STRATEGY_LIBRARY.get(clean_spaces(subject).lower(), STRATEGY_LIBRARY["generic"])


def get_scene_types_for_concept(subject: str, concept_text: str = "", requested_depth: str = "normal") -> list[str]:
    text = clean_spaces(concept_text).lower()
    subject_key = clean_spaces(subject).lower()
    if subject_key == "math":
        if "slope" in text:
            return ["graph_intuition", "rise_run_compare", "symbolic_formalize"]
        if any(term in text for term in ["triangle", "angle", "geometry"]):
            return ["geometry_construction", "comparison_transform", "symbolic_formalize"]
        if any(term in text for term in ["integral", "derivative", "rate", "accumulation"]):
            return ["graph_intuition", "comparison_transform", "symbolic_formalize"]
    if subject_key == "physics":
        if "projectile" in text or "trajectory" in text:
            return ["motion_arc", "vector_decomposition", "graph_connection"]
        return ["vector_decomposition", "system_interaction", "graph_connection"]
    if subject_key == "biology":
        if "blood" in text or "circulation" in text:
            return ["system_flow", "cycle_flow", "comparison_transform"]
        return ["system_flow", "layered_structure", "cycle_flow"]
    if subject_key == "chemistry":
        return ["structure_compare", "particle_motion", "reaction_flow"]
    if subject_key == "cs":
        if "bfs" in text or "queue" in text or "graph" in text:
            return ["graph_traversal", "queue_frontier", "state_transition"]
        return ["data_flow", "state_transition", "comparison_transform"]
    scene_types = list(STRATEGY_LIBRARY.get(subject_key, STRATEGY_LIBRARY["generic"]).preferred_scene_types)
    if requested_depth == "detailed":
        scene_types.append("comparison_transform")
    return scene_types


def should_use_equations_early(subject: str, concept_text: str = "") -> bool:
    text = clean_spaces(concept_text).lower()
    if subject == "math" and any(term in text for term in ["solve", "identity", "simplify", "algebraic", "symbolic"]):
        return True
    return False


def recommended_motion_patterns(subject: str, concept_text: str = "") -> list[str]:
    strategy = get_strategy_for_subject(subject, concept_text)
    patterns = list(strategy.motion_patterns)
    text = clean_spaces(concept_text).lower()
    if "slope" in text:
        return ["trace_then_label", "compare_and_merge", "graph_to_equation"]
    if "projectile" in text:
        return ["trace_then_label", "decompose_and_rebuild", "object_to_symbol"]
    if "bfs" in text:
        return ["focus_shift", "decompose_and_rebuild", "reveal_then_transform"]
    return patterns
