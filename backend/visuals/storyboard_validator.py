from __future__ import annotations

from dataclasses import dataclass, field

from .storyboard_schema import ScenePlan, Storyboard, clean_spaces
from .utils.animation_patterns import recommended_patterns_for_scene
from .utils.layout_variation import vary_scene_composition
from .utils.scene_timing import estimate_scene_duration, normalize_requested_depth
from .visual_strategy import get_scene_types_for_concept, should_use_equations_early


TEXT_HEAVY_MARKERS = {
    "full sentence",
    "paragraph",
    "multi-line text",
    "text heavy",
    "bullet list",
    "answer text",
    "verbatim",
}
MOTION_KEYWORDS = {
    "transform",
    "morph",
    "move",
    "trace",
    "compare",
    "shift",
    "decompose",
    "rebuild",
    "merge",
    "expand",
    "split",
    "highlight",
    "rotate",
    "sweep",
    "flow",
}
GENERIC_FLOW_MARKERS = {
    "show the concept",
    "animate the idea",
    "display the explanation",
    "fade in the text",
    "write the answer",
    "show the answer",
}


@dataclass(slots=True)
class StoryboardScore:
    visual_diversity_score: float
    motion_intent_score: float
    pedagogical_score: float
    equation_restraint_score: float
    layout_variation_score: float
    overall_score: float

    def to_dict(self) -> dict[str, float]:
        return {
            "visual_diversity_score": self.visual_diversity_score,
            "motion_intent_score": self.motion_intent_score,
            "pedagogical_score": self.pedagogical_score,
            "equation_restraint_score": self.equation_restraint_score,
            "layout_variation_score": self.layout_variation_score,
            "overall_score": self.overall_score,
        }


@dataclass(slots=True)
class ValidationResult:
    storyboard: Storyboard
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: StoryboardScore | None = None
    was_repaired: bool = False

    @property
    def is_valid(self) -> bool:
        return bool(self.score) and self.score.overall_score >= 0.62 and not self.issues


def _answer_sentences(teaching_answer: str) -> list[str]:
    raw = clean_spaces(teaching_answer)
    if not raw:
        return []
    parts = [clean_spaces(item) for item in raw.replace("?", ".").replace("!", ".").split(".") if clean_spaces(item)]
    return parts[:8]


def _token_set(text: str) -> set[str]:
    return {token for token in clean_spaces(text).lower().split() if len(token) > 2}


def _scene_is_text_heavy(scene: ScenePlan) -> bool:
    text_usage = clean_spaces(scene.text_usage).lower()
    if any(marker in text_usage for marker in TEXT_HEAVY_MARKERS):
        return True
    long_objects = sum(1 for item in scene.key_visual_objects if len(clean_spaces(item).split()) >= 6)
    return long_objects >= 3


def _scene_has_motion(scene: ScenePlan) -> bool:
    blob = clean_spaces(" ".join(scene.animation_flow + [scene.scene_goal, scene.transitions])).lower()
    return any(keyword in blob for keyword in MOTION_KEYWORDS)


def _scene_has_vague_flow(scene: ScenePlan) -> bool:
    if not scene.animation_flow:
        return True
    blob = clean_spaces(" ".join(scene.animation_flow)).lower()
    return any(marker in blob for marker in GENERIC_FLOW_MARKERS)


def _scene_uses_equation(scene: ScenePlan) -> bool:
    usage = clean_spaces(scene.equations_usage).lower()
    return usage not in {"", "none", "no equations", "labels only"}


def _scene_reads_like_answer(scene: ScenePlan, answer_sentences: list[str]) -> bool:
    goal_tokens = _token_set(scene.scene_goal)
    if not goal_tokens:
        return False
    for sentence in answer_sentences:
        sentence_tokens = _token_set(sentence)
        if not sentence_tokens:
            continue
        overlap = len(goal_tokens & sentence_tokens) / max(1, len(goal_tokens | sentence_tokens))
        if overlap >= 0.75 and not _scene_has_motion(scene):
            return True
    return False


def detect_rigidity(storyboard: Storyboard, teaching_answer: str = "") -> list[str]:
    scenes = storyboard.scene_sequence
    if not scenes:
        return ["Storyboard has no scenes."]
    issues: list[str] = []
    text_heavy_count = sum(1 for scene in scenes if _scene_is_text_heavy(scene))
    if text_heavy_count / max(1, len(scenes)) > 0.35:
        issues.append("More than 35% of scenes are text-heavy.")
    unique_layouts = {clean_spaces(scene.layout_hint).lower() for scene in scenes if clean_spaces(scene.layout_hint)}
    if len(unique_layouts) <= 1:
        issues.append("Every scene uses the same layout_hint.")
    if all(_scene_uses_equation(scene) for scene in scenes):
        issues.append("Every scene introduces equations.")
    if not any(_scene_has_motion(scene) for scene in scenes):
        issues.append("No scene contains a meaningful transform, comparison, or motion relationship.")
    if all(_scene_has_vague_flow(scene) for scene in scenes):
        issues.append("Animation flow is generic or vague across the storyboard.")
    answer_sentences = _answer_sentences(teaching_answer)
    if answer_sentences and sum(1 for scene in scenes if _scene_reads_like_answer(scene, answer_sentences)) >= max(2, len(scenes) // 2):
        issues.append("Scenes read like answer sentences instead of visual teaching moves.")
    return issues


def score_storyboard(storyboard: Storyboard, *, subject: str = "", teaching_answer: str = "") -> StoryboardScore:
    scenes = storyboard.scene_sequence
    if not scenes:
        return StoryboardScore(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    unique_scene_types = len({scene.scene_type for scene in scenes}) / len(scenes)
    unique_roles = len({scene.pedagogical_role for scene in scenes}) / len(scenes)
    unique_layouts = len({scene.layout_hint for scene in scenes}) / len(scenes)
    motion_ratio = sum(1 for scene in scenes if _scene_has_motion(scene) and not _scene_has_vague_flow(scene)) / len(scenes)
    text_penalty = sum(1 for scene in scenes if _scene_is_text_heavy(scene)) / len(scenes)
    answer_penalty = sum(1 for scene in scenes if _scene_reads_like_answer(scene, _answer_sentences(teaching_answer))) / len(scenes)

    visual_diversity_score = max(0.0, min(1.0, (unique_scene_types + unique_roles + (1.0 - text_penalty)) / 3))
    motion_intent_score = max(0.0, min(1.0, motion_ratio))

    first_scene = scenes[0]
    late_equation_ok = True
    if not should_use_equations_early(subject, storyboard.concept_summary):
        late_equation_ok = not _scene_uses_equation(first_scene)
    equation_restraint_score = 1.0 if late_equation_ok and not all(_scene_uses_equation(scene) for scene in scenes) else 0.35

    pedagogical_signals = 0.35
    if clean_spaces(first_scene.pedagogical_role).lower() in {"intuition", "hook"}:
        pedagogical_signals += 0.25
    if any(clean_spaces(scene.pedagogical_role).lower() in {"comparison", "mechanism"} for scene in scenes[1:]):
        pedagogical_signals += 0.2
    if any(clean_spaces(scene.pedagogical_role).lower() in {"formalize", "application", "transfer"} for scene in scenes[1:]):
        pedagogical_signals += 0.2
    pedagogical_signals -= min(0.3, answer_penalty * 0.5)
    pedagogical_score = max(0.0, min(1.0, pedagogical_signals))

    layout_variation_score = max(0.0, min(1.0, unique_layouts))
    overall = round(
        max(
            0.0,
            min(
                1.0,
                (
                    visual_diversity_score
                    + motion_intent_score
                    + pedagogical_score
                    + equation_restraint_score
                    + layout_variation_score
                )
                / 5,
            ),
        ),
        3,
    )
    return StoryboardScore(
        visual_diversity_score=round(visual_diversity_score, 3),
        motion_intent_score=round(motion_intent_score, 3),
        pedagogical_score=round(pedagogical_score, 3),
        equation_restraint_score=round(equation_restraint_score, 3),
        layout_variation_score=round(layout_variation_score, 3),
        overall_score=overall,
    )


def enforce_visual_diversity(storyboard: Storyboard, *, subject: str = "", requested_depth: str = "normal") -> Storyboard:
    scenes: list[ScenePlan] = []
    recent_layouts: list[str] = []
    preferred_types = get_scene_types_for_concept(subject, storyboard.concept_summary, requested_depth)
    for index, original in enumerate(storyboard.scene_sequence):
        scene = ScenePlan(**original.to_dict())
        scene.layout_hint = vary_scene_composition(scene.scene_type, subject, index, len(storyboard.scene_sequence), recent_layouts)
        recent_layouts.append(scene.layout_hint)
        if len(recent_layouts) > 3:
            recent_layouts.pop(0)
        if not _scene_has_motion(scene):
            recommended = recommended_patterns_for_scene(scene.scene_type, subject)
            scene.animation_flow = [
                f"{step.replace('_', ' ')} with {', '.join(scene.key_visual_objects[:2]) or 'the main visual'}"
                for step in recommended[:2]
            ]
        if _scene_is_text_heavy(scene):
            scene.text_usage = "minimal labels only"
        if index == 0 and _scene_uses_equation(scene) and not should_use_equations_early(subject, storyboard.concept_summary):
            scene.equations_usage = "none"
        if not scene.scene_type or scene.scene_type == "concept_metaphor" and index < len(preferred_types):
            scene.scene_type = preferred_types[index]
        scene.estimated_duration = estimate_scene_duration(
            requested_depth,
            index,
            len(storyboard.scene_sequence),
            scene.pedagogical_role,
        )
        scenes.append(scene)
    return Storyboard(
        concept_summary=storyboard.concept_summary,
        teaching_goal=storyboard.teaching_goal,
        visual_strategy=storyboard.visual_strategy,
        pacing_style=storyboard.pacing_style,
        scene_sequence=scenes,
        subject=storyboard.subject or subject,
        requested_depth=normalize_requested_depth(requested_depth),
        preferred_style=storyboard.preferred_style,
        quality_scores=dict(storyboard.quality_scores),
    )


def validate_storyboard(
    storyboard: Storyboard,
    *,
    teaching_answer: str = "",
    subject: str = "",
    requested_depth: str = "normal",
    auto_repair: bool = True,
) -> ValidationResult:
    active = storyboard
    issues = detect_rigidity(active, teaching_answer)
    repaired = False
    if issues and auto_repair:
        active = enforce_visual_diversity(active, subject=subject or active.subject, requested_depth=requested_depth)
        repaired = True
        issues = detect_rigidity(active, teaching_answer)
    score = score_storyboard(active, subject=subject or active.subject, teaching_answer=teaching_answer)
    active.quality_scores = score.to_dict()
    return ValidationResult(
        storyboard=active,
        issues=issues,
        warnings=[],
        score=score,
        was_repaired=repaired,
    )

