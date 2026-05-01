from __future__ import annotations

from typing import Any

from .validators import clean_spaces


def default_teaching_session_state() -> dict[str, Any]:
    return {
        "lastQuestion": "",
        "lastIntent": "",
        "lastExplanation": "",
        "lastSpokenSegments": [],
        "lastFormulae": [],
        "lastFunctions": [],
        "lastFrames": [],
        "lastFrameSequence": [],
        "lastVisualizerOutputs": [],
        "lastLessonTimestamps": [],
        "lastChosenVisualizer": "",
        "lastNormalizedQuestion": "",
        "lastSceneOutput": {},
        "lastSyncedPresentation": {},
        "lastPipelineDebug": {},
    }


def normalize_teaching_session_state(raw: Any) -> dict[str, Any]:
    base = default_teaching_session_state()
    if not isinstance(raw, dict):
        return base
    normalized = dict(base)
    normalized["lastQuestion"] = clean_spaces(raw.get("lastQuestion"))
    normalized["lastIntent"] = clean_spaces(raw.get("lastIntent"))
    normalized["lastExplanation"] = clean_spaces(raw.get("lastExplanation"))
    normalized["lastSpokenSegments"] = [item for item in (raw.get("lastSpokenSegments") or []) if isinstance(item, dict)]
    normalized["lastFormulae"] = [clean_spaces(item) for item in (raw.get("lastFormulae") or []) if clean_spaces(item)]
    normalized["lastFunctions"] = [item for item in (raw.get("lastFunctions") or []) if isinstance(item, dict)]
    normalized["lastFrames"] = [item for item in (raw.get("lastFrames") or []) if isinstance(item, dict)]
    normalized["lastFrameSequence"] = [item for item in (raw.get("lastFrameSequence") or []) if isinstance(item, dict)]
    normalized["lastVisualizerOutputs"] = [item for item in (raw.get("lastVisualizerOutputs") or []) if isinstance(item, dict)]
    normalized["lastLessonTimestamps"] = [
        {"start": clean_spaces(item.get("start")), "end": clean_spaces(item.get("end"))}
        for item in (raw.get("lastLessonTimestamps") or [])
        if isinstance(item, dict) and clean_spaces(item.get("start")) and clean_spaces(item.get("end"))
    ]
    normalized["lastChosenVisualizer"] = clean_spaces(raw.get("lastChosenVisualizer"))
    normalized["lastNormalizedQuestion"] = clean_spaces(raw.get("lastNormalizedQuestion"))
    normalized["lastSceneOutput"] = raw.get("lastSceneOutput") if isinstance(raw.get("lastSceneOutput"), dict) else {}
    normalized["lastSyncedPresentation"] = raw.get("lastSyncedPresentation") if isinstance(raw.get("lastSyncedPresentation"), dict) else {}
    normalized["lastPipelineDebug"] = raw.get("lastPipelineDebug") if isinstance(raw.get("lastPipelineDebug"), dict) else {}
    return normalized


def get_teaching_session_state(session: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(session, dict):
        return default_teaching_session_state()
    state = normalize_teaching_session_state(session.get("teaching_session_state"))
    session["teaching_session_state"] = state
    return state


def remember_teaching_session_state(
    current_state: dict[str, Any] | None,
    *,
    question: str,
    intent: dict[str, Any],
    explanation: str,
    spoken_segments: list[dict[str, Any]],
    formulae: list[str],
    functions: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    frame_sequence: list[dict[str, Any]],
    visualizer_outputs: list[dict[str, Any]],
    chosen_visualizer: str,
    lesson_timestamps: list[dict[str, str]],
    scene_output: dict[str, Any],
    synced_presentation: dict[str, Any],
    pipeline_debug: dict[str, Any],
) -> dict[str, Any]:
    state = normalize_teaching_session_state(current_state)
    state["lastQuestion"] = clean_spaces(question)
    state["lastIntent"] = clean_spaces(intent.get("mode"))
    state["lastNormalizedQuestion"] = clean_spaces(intent.get("normalizedQuestion"))
    state["lastExplanation"] = clean_spaces(explanation)
    state["lastSpokenSegments"] = [item for item in spoken_segments if isinstance(item, dict)]
    state["lastFormulae"] = [clean_spaces(item) for item in formulae if clean_spaces(item)]
    state["lastFunctions"] = [item for item in functions if isinstance(item, dict)]
    state["lastFrames"] = [item for item in frames if isinstance(item, dict)]
    state["lastFrameSequence"] = [item for item in frame_sequence if isinstance(item, dict)]
    state["lastVisualizerOutputs"] = [item for item in visualizer_outputs if isinstance(item, dict)]
    state["lastLessonTimestamps"] = [
        {"start": clean_spaces(item.get("start")), "end": clean_spaces(item.get("end"))}
        for item in lesson_timestamps
        if isinstance(item, dict) and clean_spaces(item.get("start")) and clean_spaces(item.get("end"))
    ]
    state["lastChosenVisualizer"] = clean_spaces(chosen_visualizer)
    state["lastSceneOutput"] = scene_output if isinstance(scene_output, dict) else {}
    state["lastSyncedPresentation"] = synced_presentation if isinstance(synced_presentation, dict) else {}
    state["lastPipelineDebug"] = pipeline_debug if isinstance(pipeline_debug, dict) else {}
    return state


def repeat_state_available(state: dict[str, Any] | None) -> bool:
    normalized = normalize_teaching_session_state(state)
    return bool(normalized["lastExplanation"] and normalized["lastSpokenSegments"] and normalized["lastFrames"] and normalized["lastVisualizerOutputs"])
