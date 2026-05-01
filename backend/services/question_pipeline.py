from __future__ import annotations

from typing import Any

from .openai_manim_pipeline import (
    call_openai_manim_pipeline,
    fallback_openai_manim_output,
    normalize_openai_manim_output,
    openai_pipeline_status,
)
from .presentation_sync import build_compat_lesson_plan, build_synced_presentation, build_visual_payload, concat_spoken_answer
from .session_state import normalize_teaching_session_state, remember_teaching_session_state, repeat_state_available
from .validators import clean_spaces, parse_timecode, sentence_case, trim_sentence


def _default_suggestions(raw: list[str]) -> list[str]:
    suggestions = [trim_sentence(item, 72) for item in raw if clean_spaces(item)]
    return (suggestions or ["Explain more slowly", "Show another example", "Repeat the key idea"])[:4]


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _intent(question: str, preferred_visualization: str = "") -> dict[str, Any]:
    text = clean_spaces(question)
    lowered = text.lower()
    wants_repeat = any(term in lowered for term in ["repeat", "again", "say that again"])
    wants_visuals = True
    wants_formulae = any(term in lowered for term in ["formula", "equation", "derive", "solve"])
    wants_graph = any(term in lowered for term in ["graph", "plot", "curve", "axis", "function"])
    if wants_repeat:
        mode = "repeat_previous"
    elif any(term in lowered for term in ["brief", "quick", "short"]):
        mode = "brief_explain"
    elif any(term in lowered for term in ["visual", "show", "draw", "animate"]):
        mode = "visualize"
    else:
        mode = "simple_explain"
    return {
        "rawQuestion": text,
        "normalizedQuestion": text,
        "mode": mode,
        "wantsVisuals": wants_visuals,
        "wantsRepeat": wants_repeat,
        "wantsFormulae": wants_formulae,
        "wantsFunctionGraph": wants_graph,
        "useRealLifeExample": "example" in lowered,
        "preferredVisualization": clean_spaces(preferred_visualization) or "manim",
    }


def _speech_to_teaching_segments(scene_output: dict[str, Any]) -> list[dict[str, Any]]:
    frames = [item for item in (scene_output.get("frames") or []) if isinstance(item, dict)]
    segments = []
    for index, item in enumerate(scene_output.get("spokenAnswerSegments") or [], start=1):
        if not isinstance(item, dict):
            continue
        frame = frames[index - 1] if index - 1 < len(frames) else {}
        start = clean_spaces(item.get("start"))
        end = clean_spaces(item.get("end"))
        duration = max(1, parse_timecode(end) - parse_timecode(start))
        segments.append(
            {
                "segment_id": clean_spaces(item.get("id")) or f"segment_{index}",
                "step_id": clean_spaces(item.get("id")) or f"step_{index}",
                "label": trim_sentence(frame.get("sceneDescription") or item.get("purpose") or f"Beat {index}", 52),
                "speech_text": sentence_case(trim_sentence(item.get("text"), 420)),
                "frame_goal": sentence_case(trim_sentence(frame.get("visualGoal") or item.get("text"), 220)),
                "timing_hint": {"target_duration_sec": duration, "pace": "medium"},
                "timeline": {"start": start, "end": end},
                "purpose": clean_spaces(item.get("purpose")) or "core_explanation",
                "formulae": [trim_sentence(formula, 120) for formula in (frame.get("formulae") or []) if clean_spaces(formula)],
                "functions": [],
                "visualizer": "manim",
            }
        )
    return segments


def _build_scene_output(normalized: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    speech_segments = []
    frames = []
    normalized_segments = [item for item in ((normalized.get("speech") or {}).get("segments") or []) if isinstance(item, dict)]
    normalized_frames = [item for item in ((normalized.get("manim") or {}).get("frames") or []) if isinstance(item, dict)]
    for index, item in enumerate(normalized_segments, start=1):
        speech_segments.append(
            {
                "id": clean_spaces(item.get("id")) or f"segment_{index}",
                "start": clean_spaces(item.get("start")),
                "end": clean_spaces(item.get("end")),
                "text": sentence_case(trim_sentence(item.get("text"), 420)),
                "purpose": clean_spaces(item.get("purpose")) or "core_explanation",
            }
        )
    for index, frame in enumerate(normalized_frames, start=1):
        frames.append(
            {
                "id": clean_spaces(frame.get("id")) or f"frame_{index}",
                "sceneDescription": sentence_case(trim_sentence(frame.get("title") or frame.get("scene_goal"), 220)),
                "timelineStart": clean_spaces(frame.get("start")),
                "timelineEnd": clean_spaces(frame.get("end")),
                "formulae": [trim_sentence(item, 120) for item in (normalized.get("formulae") or []) if clean_spaces(item)][:3],
                "functionsToShow": [],
                "functionsToDraw": [],
                "visualizer": "manim",
                "visualGoal": sentence_case(trim_sentence(frame.get("scene_goal"), 220)),
                "visualNotes": [sentence_case(trim_sentence(frame.get("layout_notes"), 160))],
                "analogy": "",
                "elementsNeeded": [],
            }
        )
    return {
        "answerMode": clean_spaces(intent.get("mode")) or "simple_explain",
        "spokenAnswerSegments": speech_segments,
        "formulae": normalized.get("formulae") or [],
        "functions": [],
        "frames": frames,
    }


def _build_frame_sequence(normalized: dict[str, Any], scene_output: dict[str, Any], question: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frames = [item for item in ((normalized.get("manim") or {}).get("frames") or []) if isinstance(item, dict)]
    scene_frames = [item for item in (scene_output.get("frames") or []) if isinstance(item, dict)]
    frame_sequence: list[dict[str, Any]] = []
    visualizer_outputs: list[dict[str, Any]] = []
    for index, frame in enumerate(frames, start=1):
        scene_frame = scene_frames[index - 1] if index - 1 < len(scene_frames) else {}
        segment_id = clean_spaces(frame.get("speech_segment_id")) or f"segment_{index}"
        duration = max(1.0, _safe_float(frame.get("duration_sec"), 6.0))
        renderer_payload = {
            "renderer_version": "openai_direct_manim_v1",
            "scene_type": "openai_direct",
            "scene_class_name": "ParalleaGeneratedScene",
            "manim_code": frame.get("code") or "",
            "title": frame.get("title") or normalized.get("title") or question,
            "subtitle": frame.get("scene_goal") or scene_frame.get("visualGoal"),
            "duration_sec": duration,
            "timeline_start": frame.get("start"),
            "timeline_end": frame.get("end"),
            "speech_segment_id": segment_id,
        }
        visualizer_output = {
            "frameId": clean_spaces(frame.get("id")) or f"frame_{index}",
            "visualizer": "manim",
            "plan": {
                "source": "openai",
                "sceneClassName": "ParalleaGeneratedScene",
                "durationSec": duration,
                "timelineStart": frame.get("start"),
                "timelineEnd": frame.get("end"),
            },
        }
        visualizer_outputs.append(visualizer_output)
        frame_sequence.append(
            {
                "frame_number": index,
                "segment_id": segment_id,
                "speech_segment_ref": segment_id,
                "title": trim_sentence(frame.get("title") or normalized.get("title") or f"Frame {index}", 56),
                "render_mode": "manim",
                "reason": "Use the OpenAI-generated Manim clip for the matching spoken timestamp.",
                "scene_goal": sentence_case(trim_sentence(frame.get("scene_goal") or scene_frame.get("visualGoal"), 220)),
                "notes_for_sync": "Play this clip with the matching speech segment.",
                "layout_notes": sentence_case(trim_sentence(frame.get("layout_notes"), 180)),
                "object_actions": [],
                "selected_asset_ids": [],
                "visual_assets": [],
                "renderer_payload": renderer_payload,
                "payload": renderer_payload,
                "timeline_start": clean_spaces(frame.get("start")),
                "timeline_end": clean_spaces(frame.get("end")),
                "formulae": scene_frame.get("formulae") or [],
                "functions": [],
                "visualizer_output": visualizer_output,
                "fallback_mode": "excalidraw",
                "fallback": {},
                "visual_pipeline_path": "openai_single_call_manim",
                "debug": {"question": trim_sentence(question, 220), "source": "openai_direct_manim"},
            }
        )
    return frame_sequence, visualizer_outputs


async def build_question_pipeline(
    *,
    question: str,
    context: str,
    title: str,
    learner_request: str = "",
    pedagogy_mode: str = "simple",
    persona_context: str = "",
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = normalize_teaching_session_state(session_state)
    raw_question = learner_request or question
    intent = _intent(raw_question, preferred_visualization)
    if clean_spaces(intent.get("mode")) == "repeat_previous" and repeat_state_available(state):
        scene_output = state.get("lastSceneOutput") if isinstance(state.get("lastSceneOutput"), dict) else {}
        frame_sequence = [item for item in (state.get("lastFrameSequence") or []) if isinstance(item, dict)]
        visualizer_outputs = [item for item in (state.get("lastVisualizerOutputs") or []) if isinstance(item, dict)]
        synced_presentation = state.get("lastSyncedPresentation") if isinstance(state.get("lastSyncedPresentation"), dict) else build_synced_presentation(scene_output, visualizer_outputs)
        teaching_segments = _speech_to_teaching_segments(scene_output)
        answer = clean_spaces(state.get("lastExplanation")) or concat_spoken_answer(scene_output.get("spokenAnswerSegments") or [])
        suggestions = ["Repeat this again", "Explain one part more slowly", "Show another example"]
        follow_up = "Which part should I explain more slowly?"
        visual_payload = build_visual_payload(frame_sequence)
        lesson_timestamps = state.get("lastLessonTimestamps") or [
            {"start": clean_spaces(item.get("start")), "end": clean_spaces(item.get("end"))}
            for item in (scene_output.get("spokenAnswerSegments") or [])
            if isinstance(item, dict) and clean_spaces(item.get("start")) and clean_spaces(item.get("end"))
        ]
        pipeline_debug = {
            "provider": "state_replay",
            "openai": openai_pipeline_status(),
            "intent": intent,
            "normalizedQuestion": state.get("lastNormalizedQuestion") or state.get("lastQuestion") or question,
            "openaiOutput": {},
            "visualizersByFrame": [
                {
                    "frameId": clean_spaces(frame.get("segment_id") or frame.get("frame_number")),
                    "visualizer": clean_spaces(frame.get("render_mode")) or "manim",
                    "formulae": frame.get("formulae") or [],
                    "functions": frame.get("functions") or [],
                    "selectedExcalidrawElements": frame.get("selected_asset_ids") or [],
                }
                for frame in frame_sequence
            ],
            "previousStateReuse": {
                "explanationReused": True,
                "sceneReused": True,
                "repeatStateAvailable": True,
            },
        }
        next_session_state = remember_teaching_session_state(
            state,
            question=clean_spaces(state.get("lastQuestion")) or question,
            intent=intent,
            explanation=answer,
            spoken_segments=scene_output.get("spokenAnswerSegments") or [],
            formulae=scene_output.get("formulae") or [],
            functions=scene_output.get("functions") or [],
            frames=scene_output.get("frames") or [],
            frame_sequence=frame_sequence,
            visualizer_outputs=visualizer_outputs,
            chosen_visualizer="manim",
            lesson_timestamps=lesson_timestamps,
            scene_output=scene_output,
            synced_presentation=synced_presentation,
            pipeline_debug=pipeline_debug,
        )
        compat_lesson_plan = build_compat_lesson_plan(
            title=title,
            explanation=answer,
            scene_output=scene_output,
            follow_up=follow_up,
            suggestions=suggestions,
        )
        return {
            "answer": sentence_case(trim_sentence(answer, 1400)),
            "follow_up": sentence_case(trim_sentence(follow_up, 140)),
            "suggestions": suggestions[:4],
            "intent": intent,
            "first_pass_explanation": {"provider": "state_replay", "explanation": answer, "formulae": scene_output.get("formulae") or []},
            "scene_output": scene_output,
            "teaching_segments": teaching_segments,
            "frame_sequence": frame_sequence,
            "visual_payload": visual_payload,
            "synced_presentation": synced_presentation,
            "lesson_plan": compat_lesson_plan,
            "segment_plan": {
                "lesson_title": trim_sentence(title or question, 56),
                "segmentation_strategy": "Replay of the last OpenAI-generated speech and Manim timestamp plan.",
                "segments": teaching_segments,
            },
            "teaching_session_state": next_session_state,
            "pipeline_debug": pipeline_debug,
        }
    raw = await call_openai_manim_pipeline(
        question=clean_spaces(question),
        context=context,
        title=title,
        learner_request=raw_question,
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
    )
    source = "openai" if raw else "local_openai_shape_fallback"
    normalized = normalize_openai_manim_output(
        raw or fallback_openai_manim_output(question, context, title),
        question=question,
        context=context,
        title=title,
    )
    scene_output = _build_scene_output(normalized, intent)
    frame_sequence, visualizer_outputs = _build_frame_sequence(normalized, scene_output, question)
    synced_presentation = build_synced_presentation(scene_output, visualizer_outputs)
    teaching_segments = _speech_to_teaching_segments(scene_output)
    answer = concat_spoken_answer(scene_output.get("spokenAnswerSegments") or []) or clean_spaces(normalized.get("answer"))
    suggestions = _default_suggestions(normalized.get("suggestions") or [])
    follow_up = clean_spaces(normalized.get("follow_up")) or "What should I explain next?"
    visual_payload = build_visual_payload(frame_sequence)
    lesson_timestamps = [
        {"start": clean_spaces(item.get("start")), "end": clean_spaces(item.get("end"))}
        for item in (scene_output.get("spokenAnswerSegments") or [])
        if isinstance(item, dict) and clean_spaces(item.get("start")) and clean_spaces(item.get("end"))
    ]
    pipeline_debug = {
        "provider": source,
        "openai": openai_pipeline_status(),
        "intent": intent,
        "normalizedQuestion": intent.get("normalizedQuestion"),
        "openaiOutput": {
            "title": normalized.get("title"),
            "speechSegments": normalized.get("speech", {}).get("segments", []),
            "manimFrameCount": len((normalized.get("manim") or {}).get("frames") or []),
            "codeWarnings": (normalized.get("debug") or {}).get("codeWarnings") or [],
        },
        "visualizersByFrame": [
            {
                "frameId": clean_spaces(frame.get("segment_id") or frame.get("frame_number")),
                "visualizer": "manim",
                "formulae": frame.get("formulae") or [],
                "functions": [],
                "selectedExcalidrawElements": [],
            }
            for frame in frame_sequence
        ],
        "previousStateReuse": {
            "explanationReused": False,
            "sceneReused": False,
            "repeatStateAvailable": bool(state.get("lastSpokenSegments")),
        },
    }
    next_session_state = remember_teaching_session_state(
        state,
        question=clean_spaces(question),
        intent=intent,
        explanation=answer,
        spoken_segments=scene_output.get("spokenAnswerSegments") or [],
        formulae=scene_output.get("formulae") or [],
        functions=[],
        frames=scene_output.get("frames") or [],
        frame_sequence=frame_sequence,
        visualizer_outputs=visualizer_outputs,
        chosen_visualizer="manim",
        lesson_timestamps=lesson_timestamps,
        scene_output=scene_output,
        synced_presentation=synced_presentation,
        pipeline_debug=pipeline_debug,
    )
    compat_lesson_plan = build_compat_lesson_plan(
        title=normalized.get("title") or title,
        explanation=answer,
        scene_output=scene_output,
        follow_up=follow_up,
        suggestions=suggestions,
    )
    return {
        "answer": sentence_case(trim_sentence(answer, 1400)),
        "follow_up": sentence_case(trim_sentence(follow_up, 140)),
        "suggestions": suggestions[:4],
        "intent": intent,
        "first_pass_explanation": {"provider": source, "explanation": answer, "formulae": scene_output.get("formulae") or []},
        "scene_output": scene_output,
        "teaching_segments": teaching_segments,
        "frame_sequence": frame_sequence,
        "visual_payload": visual_payload,
        "synced_presentation": synced_presentation,
        "lesson_plan": compat_lesson_plan,
        "segment_plan": {
            "lesson_title": trim_sentence(normalized.get("title") or title or question, 56),
            "segmentation_strategy": "Single OpenAI call generated speech and matching Manim clips with timestamps.",
            "segments": teaching_segments,
        },
        "teaching_session_state": next_session_state,
        "pipeline_debug": pipeline_debug,
    }
