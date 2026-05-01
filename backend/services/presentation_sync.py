from __future__ import annotations

from typing import Any

from .validators import clean_spaces, parse_timecode, sentence_case, trim_sentence


def concat_spoken_answer(spoken_segments: list[dict[str, Any]]) -> str:
    return " ".join(clean_spaces(item.get("text")) for item in spoken_segments if clean_spaces(item.get("text"))).strip()


def build_teaching_segments(scene_output: dict[str, Any]) -> list[dict[str, Any]]:
    frames_by_index = {index + 1: frame for index, frame in enumerate(scene_output.get("frames") or []) if isinstance(frame, dict)}
    segments = []
    for index, item in enumerate(scene_output.get("spokenAnswerSegments") or [], start=1):
        if not isinstance(item, dict):
            continue
        frame = frames_by_index.get(index, {})
        start = clean_spaces(item.get("start"))
        end = clean_spaces(item.get("end"))
        start_seconds = parse_timecode(start)
        end_seconds = parse_timecode(end)
        duration = max(1, end_seconds - start_seconds) if start_seconds >= 0 and end_seconds >= 0 else 6
        segments.append(
            {
                "segment_id": clean_spaces(item.get("id")) or f"segment_{index}",
                "step_id": clean_spaces(item.get("id")) or f"step_{index}",
                "label": trim_sentence(frame.get("sceneDescription") or item.get("purpose") or f"Beat {index}", 52),
                "speech_text": sentence_case(trim_sentence(item.get("text"), 360)),
                "frame_goal": sentence_case(trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription") or item.get("text"), 220)),
                "timing_hint": {"target_duration_sec": duration, "pace": "medium"},
                "timeline": {"start": start, "end": end},
                "purpose": clean_spaces(item.get("purpose")) or "core_explanation",
                "formulae": [trim_sentence(formula, 120) for formula in (frame.get("formulae") or []) if clean_spaces(formula)],
                "functions": [fn for fn in (frame.get("functionsToShow") or []) if isinstance(fn, dict)],
                "visualizer": clean_spaces(frame.get("visualizer")) or "excalidraw",
            }
        )
    return segments


def build_visual_payload(frames: list[dict[str, Any]]) -> dict[str, Any]:
    if not frames:
        return {"segments": []}
    start_seconds = [parse_timecode(frame.get("timeline_start") or frame.get("timelineStart")) for frame in frames]
    end_seconds = [parse_timecode(frame.get("timeline_end") or frame.get("timelineEnd")) for frame in frames]
    valid_starts = [value for value in start_seconds if value >= 0]
    valid_ends = [value for value in end_seconds if value >= 0]
    total_duration = max(valid_ends or [1]) - min(valid_starts or [0])
    total_duration = max(1, total_duration)
    segments = []
    baseline = min(valid_starts or [0])
    for frame in frames:
        start = parse_timecode(frame.get("timeline_start") or frame.get("timelineStart"))
        end = parse_timecode(frame.get("timeline_end") or frame.get("timelineEnd"))
        if start < 0:
            start = baseline
        if end <= start:
            end = start + 1
        segments.append(
            {
                "id": clean_spaces(frame.get("segment_id") or frame.get("id")) or "segment",
                "title": trim_sentence(frame.get("title") or frame.get("scene_goal") or frame.get("sceneDescription"), 56),
                "start_pct": round((start - baseline) / total_duration, 4),
                "end_pct": round((end - baseline) / total_duration, 4),
                "kind": clean_spaces(frame.get("render_mode")) or "excalidraw",
                "payload": frame.get("payload") or frame.get("renderer_payload") or {},
                "renderer_payload": frame.get("renderer_payload") or frame.get("payload") or {},
                "frame_number": frame.get("frame_number"),
                "speech_segment_ref": frame.get("speech_segment_ref"),
                "reason": frame.get("reason"),
                "scene_goal": frame.get("scene_goal"),
                "layout_notes": frame.get("layout_notes"),
                "notes_for_sync": frame.get("notes_for_sync"),
                "selected_asset_ids": frame.get("selected_asset_ids") or [],
            }
        )
    if segments:
        segments[0]["start_pct"] = 0.0
        segments[-1]["end_pct"] = 1.0
    return {"segments": segments}


def build_synced_presentation(
    scene_output: dict[str, Any],
    visualizer_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    frames = [item for item in (scene_output.get("frames") or []) if isinstance(item, dict)]
    spoken_segments = [item for item in (scene_output.get("spokenAnswerSegments") or []) if isinstance(item, dict)]
    return {
        "spokenSegments": spoken_segments,
        "frames": frames,
        "visualizerOutputs": [item for item in visualizer_outputs if isinstance(item, dict)],
    }


def build_compat_lesson_plan(
    *,
    title: str,
    explanation: str,
    scene_output: dict[str, Any],
    follow_up: str,
    suggestions: list[str],
) -> dict[str, Any]:
    frames = [item for item in (scene_output.get("frames") or []) if isinstance(item, dict)]
    speaking = [item for item in (scene_output.get("spokenAnswerSegments") or []) if isinstance(item, dict)]
    return {
        "topic": trim_sentence(title or "Lesson", 72),
        "teaching_objective": sentence_case(trim_sentence(explanation, 220)),
        "answer_summary": sentence_case(trim_sentence(explanation, 420)),
        "teaching_style": "Structured explanation synchronized to visual frames.",
        "key_ideas": [sentence_case(trim_sentence(item.get("text"), 160)) for item in speaking[:4] if clean_spaces(item.get("text"))],
        "visualization_notes": [sentence_case(trim_sentence(item.get("visualGoal"), 160)) for item in frames[:4] if clean_spaces(item.get("visualGoal"))],
        "key_formulas": [
            {"formula": trim_sentence(item, 120), "meaning": "Show this while it is explained.", "when_to_use": "Use when this frame introduces the formula."}
            for item in (scene_output.get("formulae") or [])[:4]
            if clean_spaces(item)
        ],
        "examples": [sentence_case(trim_sentence(item.get("analogy"), 160)) for item in frames[:3] if clean_spaces(item.get("analogy"))],
        "teaching_steps": [
            {
                "step_id": clean_spaces(frame.get("id")) or f"step_{index}",
                "label": trim_sentence(frame.get("sceneDescription") or f"Step {index}", 48),
                "key_idea": sentence_case(trim_sentence(frame.get("sceneDescription"), 180)),
                "explanation": sentence_case(trim_sentence((speaking[index - 1] if index - 1 < len(speaking) else {}).get("text"), 220)),
                "visual_focus": sentence_case(trim_sentence(frame.get("visualGoal"), 180)),
                "example": sentence_case(trim_sentence(frame.get("analogy"), 160)) if clean_spaces(frame.get("analogy")) else "",
                "formula": trim_sentence((frame.get("formulae") or [""])[0], 120),
                "formula_terms": [],
                "visual_mode_hint": clean_spaces(frame.get("visualizer")) or "excalidraw",
            }
            for index, frame in enumerate(frames, start=1)
        ],
        "follow_up": sentence_case(trim_sentence(follow_up, 140)),
        "suggestions": [trim_sentence(item, 72) for item in suggestions if clean_spaces(item)][:4],
    }
