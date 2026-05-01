from __future__ import annotations

import json
import re
from typing import Any

from .schema import (
    EXCALIDRAW_SEQUENCE_ACTIONS,
    EXPLANATION_MODES,
    MANIM_OBJECT_TYPES,
    SPOKEN_PURPOSE_VALUES,
    VISUALIZER_VALUES,
)


TIMECODE_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def clean_spaces(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def trim_sentence(text: Any, limit: int = 220) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def sentence_case(text: Any) -> str:
    value = clean_spaces(text)
    if not value:
        return ""
    normalized = value[0].upper() + value[1:]
    if normalized[-1] not in ".!?":
        normalized += "."
    return normalized


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = clean_spaces(value).lower()
    return text in {"1", "true", "yes", "on"}


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def parse_json_blob(raw: Any, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    if isinstance(raw, dict):
        return raw
    text = clean_spaces(raw)
    if not text:
        return fallback
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return fallback
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else fallback
        except Exception:
            return fallback


def parse_timecode(value: Any) -> int:
    text = clean_spaces(value)
    if not TIMECODE_RE.match(text):
        return -1
    hours, minutes, seconds = [int(part) for part in text.split(":")]
    return (hours * 3600) + (minutes * 60) + seconds


def format_timecode(total_seconds: int) -> str:
    safe_seconds = max(0, int(total_seconds))
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    seconds = safe_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_duration_seconds(text: Any, *, minimum: int = 4, maximum: int = 10) -> int:
    words = len(clean_spaces(text).split())
    estimate = round(3.5 + (words / 2.6))
    return int(clamp(estimate, minimum, maximum))


def normalize_function_spec(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    label = trim_sentence(raw.get("label"), 56)
    expression = trim_sentence(raw.get("expression"), 160)
    if not label or not expression:
        return None
    return {
        "label": label,
        "expression": expression,
        "shouldShowOnScreen": safe_bool(raw.get("shouldShowOnScreen", True)),
        "shouldDrawOnGraph": safe_bool(raw.get("shouldDrawOnGraph", False)),
        "graphNotes": trim_sentence(raw.get("graphNotes"), 160) or "",
    }


def normalize_spoken_segment(raw: Any, index: int, *, previous_end: int = 0) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = sentence_case(trim_sentence(raw.get("text"), 320))
    if not text:
        return None
    purpose = clean_spaces(raw.get("purpose")).lower()
    if purpose not in SPOKEN_PURPOSE_VALUES:
        purpose = "core_explanation"
    start_seconds = parse_timecode(raw.get("start"))
    end_seconds = parse_timecode(raw.get("end"))
    if start_seconds < 0:
        start_seconds = previous_end
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + estimate_duration_seconds(text)
    end_seconds = max(end_seconds, start_seconds + 1)
    return {
        "id": clean_spaces(raw.get("id")) or f"segment_{index}",
        "start": format_timecode(start_seconds),
        "end": format_timecode(end_seconds),
        "text": text,
        "purpose": purpose,
    }


def normalize_visual_frame(
    raw: Any,
    index: int,
    *,
    mode: str,
    spoken_segments: list[dict[str, Any]],
    allowed_excalidraw_elements: set[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scene_description = sentence_case(trim_sentence(raw.get("sceneDescription"), 220))
    visual_goal = sentence_case(trim_sentence(raw.get("visualGoal"), 180))
    if not scene_description and not visual_goal:
        return None
    visualizer = clean_spaces(raw.get("visualizer")).lower()
    if visualizer not in VISUALIZER_VALUES:
        visualizer = "excalidraw"
    start_seconds = parse_timecode(raw.get("timelineStart"))
    end_seconds = parse_timecode(raw.get("timelineEnd"))
    if index - 1 < len(spoken_segments):
        segment = spoken_segments[index - 1]
        if start_seconds < 0:
            start_seconds = parse_timecode(segment.get("start"))
        if end_seconds <= start_seconds:
            end_seconds = parse_timecode(segment.get("end"))
    if start_seconds < 0:
        start_seconds = 0 if index == 1 else parse_timecode(spoken_segments[min(index - 2, len(spoken_segments) - 1)].get("end"))
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 6
    functions_to_show = [item for item in (normalize_function_spec(fn) for fn in (raw.get("functionsToShow") or [])) if item]
    functions_to_draw = [item for item in (normalize_function_spec(fn) for fn in (raw.get("functionsToDraw") or [])) if item]
    formulae = [trim_sentence(item, 120) for item in (raw.get("formulae") or []) if clean_spaces(item)][:4]
    visual_notes = [sentence_case(trim_sentence(item, 140)) for item in (raw.get("visualNotes") or []) if clean_spaces(item)][:5]
    if not visual_notes:
        visual_notes = [scene_description or visual_goal]
    elements_needed = [clean_spaces(item) for item in (raw.get("elementsNeeded") or []) if clean_spaces(item)]
    if allowed_excalidraw_elements is not None:
        elements_needed = [item for item in elements_needed if item in allowed_excalidraw_elements]
    return {
        "id": clean_spaces(raw.get("id")) or f"frame_{index}",
        "sceneDescription": scene_description or visual_goal,
        "timelineStart": format_timecode(start_seconds),
        "timelineEnd": format_timecode(end_seconds),
        "formulae": formulae,
        "functionsToShow": functions_to_show,
        "functionsToDraw": functions_to_draw,
        "visualizer": visualizer,
        "visualGoal": visual_goal or scene_description,
        "visualNotes": visual_notes,
        "analogy": sentence_case(trim_sentence(raw.get("analogy"), 160)) if clean_spaces(raw.get("analogy")) else "",
        "elementsNeeded": elements_needed if visualizer == "excalidraw" else [],
    }


def normalize_gemini_scene_output(
    raw: Any,
    *,
    fallback: dict[str, Any],
    forced_mode: str | None = None,
    allowed_excalidraw_elements: set[str] | None = None,
) -> dict[str, Any]:
    parsed = raw if isinstance(raw, dict) else {}
    if not isinstance(parsed, dict):
        return fallback
    answer_mode = clean_spaces(parsed.get("answerMode")).lower()
    if answer_mode not in EXPLANATION_MODES:
        answer_mode = clean_spaces(forced_mode).lower() if clean_spaces(forced_mode).lower() in EXPLANATION_MODES else fallback.get("answerMode", "simple_explain")
    spoken_segments: list[dict[str, Any]] = []
    previous_end = 0
    for index, item in enumerate(parsed.get("spokenAnswerSegments") or [], start=1):
        normalized = normalize_spoken_segment(item, index, previous_end=previous_end)
        if not normalized:
            continue
        start_seconds = parse_timecode(normalized["start"])
        end_seconds = parse_timecode(normalized["end"])
        if start_seconds < previous_end:
            start_seconds = previous_end
            normalized["start"] = format_timecode(start_seconds)
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + estimate_duration_seconds(normalized["text"])
            normalized["end"] = format_timecode(end_seconds)
        previous_end = parse_timecode(normalized["end"])
        spoken_segments.append(normalized)
    if not spoken_segments:
        return fallback
    functions = [item for item in (normalize_function_spec(fn) for fn in (parsed.get("functions") or [])) if item][:4]
    formulae = [trim_sentence(item, 120) for item in (parsed.get("formulae") or []) if clean_spaces(item)][:6]
    frames: list[dict[str, Any]] = []
    for index, item in enumerate(parsed.get("frames") or [], start=1):
        normalized = normalize_visual_frame(
            item,
            index,
            mode=answer_mode,
            spoken_segments=spoken_segments,
            allowed_excalidraw_elements=allowed_excalidraw_elements,
        )
        if normalized:
            frames.append(normalized)
    if not frames:
        return fallback
    while len(frames) < len(spoken_segments):
        source_segment = spoken_segments[len(frames)]
        frames.append(
            {
                "id": f"frame_{len(frames) + 1}",
                "sceneDescription": sentence_case(source_segment["text"]),
                "timelineStart": source_segment["start"],
                "timelineEnd": source_segment["end"],
                "formulae": [],
                "functionsToShow": [],
                "functionsToDraw": [],
                "visualizer": "excalidraw",
                "visualGoal": sentence_case(source_segment["text"]),
                "visualNotes": [sentence_case(source_segment["text"])],
                "analogy": "",
                "elementsNeeded": [],
            }
        )
    return {
        "answerMode": answer_mode,
        "spokenAnswerSegments": spoken_segments,
        "formulae": formulae,
        "functions": functions,
        "frames": frames[: len(spoken_segments)],
    }


def normalize_excalidraw_frame_plan(
    raw: Any,
    *,
    fallback: dict[str, Any],
    allowed_element_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    elements = []
    used = set()
    for item in raw.get("elementsToUse") or []:
        if not isinstance(item, dict):
            continue
        asset_id = clean_spaces(item.get("assetId"))
        if asset_id not in allowed_element_ids or asset_id in used:
            continue
        used.add(asset_id)
        elements.append(
            {
                "assetId": asset_id,
                "label": trim_sentence(item.get("label"), 48) or "",
                "positionHint": trim_sentence(item.get("positionHint"), 48) or "center",
                "purpose": sentence_case(trim_sentence(item.get("purpose"), 120)) or "Support the main concept.",
            }
        )
    if not elements:
        return fallback
    text_labels = [
        {
            "text": trim_sentence(item.get("text"), 120),
            "positionHint": trim_sentence(item.get("positionHint"), 48) or "bottom",
        }
        for item in (raw.get("textLabels") or [])
        if isinstance(item, dict) and clean_spaces(item.get("text"))
    ][:6]
    arrow_targets = {item["assetId"] for item in elements}
    arrows = []
    for item in raw.get("arrows") or []:
        if not isinstance(item, dict):
            continue
        from_id = clean_spaces(item.get("from"))
        to_id = clean_spaces(item.get("to"))
        if from_id not in arrow_targets or to_id not in arrow_targets or from_id == to_id:
            continue
        arrows.append({"from": from_id, "to": to_id, "label": trim_sentence(item.get("label"), 60) or ""})
    sequence = []
    for item in raw.get("sequence") or []:
        if not isinstance(item, dict):
            continue
        action = clean_spaces(item.get("action")).lower()
        targets = [clean_spaces(target) for target in (item.get("targetIds") or []) if clean_spaces(target)]
        if action not in EXCALIDRAW_SEQUENCE_ACTIONS or not targets:
            continue
        sequence.append(
            {
                "step": int(safe_float(item.get("step"), len(sequence) + 1)),
                "action": action,
                "targetIds": targets[:3],
            }
        )
    if not sequence:
        return fallback
    return {
        "frameId": clean_spaces(raw.get("frameId")) or fallback["frameId"],
        "title": trim_sentence(raw.get("title"), 72) or fallback.get("title", ""),
        "elementsToUse": elements[:3],
        "textLabels": text_labels,
        "arrows": arrows[:3],
        "sequence": sorted(sequence, key=lambda item: item["step"]),
    }


def normalize_manim_frame_plan(raw: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    objects = []
    for index, item in enumerate(raw.get("objects") or [], start=1):
        if not isinstance(item, dict):
            continue
        object_type = clean_spaces(item.get("type")).lower()
        if object_type not in MANIM_OBJECT_TYPES:
            continue
        objects.append(
            {
                "id": clean_spaces(item.get("id")) or f"obj_{index}",
                "type": object_type,
                "content": trim_sentence(item.get("content"), 140) or "",
                "expression": trim_sentence(item.get("expression"), 140) or "",
                "animation": trim_sentence(item.get("animation"), 80) or "",
                "notes": trim_sentence(item.get("notes"), 140) or "",
            }
        )
    if not objects:
        return fallback
    sequence = []
    object_ids = {item["id"] for item in objects}
    for item in raw.get("sequence") or []:
        if not isinstance(item, dict):
            continue
        target_ids = [clean_spaces(target) for target in (item.get("targetIds") or []) if clean_spaces(target) in object_ids]
        if not target_ids:
            continue
        sequence.append(
            {
                "step": int(safe_float(item.get("step"), len(sequence) + 1)),
                "action": trim_sentence(item.get("action"), 80) or "Show",
                "targetIds": target_ids[:4],
                "narrationCue": sentence_case(trim_sentence(item.get("narrationCue"), 140)) if clean_spaces(item.get("narrationCue")) else "",
            }
        )
    if not sequence:
        return fallback
    return {
        "frameId": clean_spaces(raw.get("frameId")) or fallback["frameId"],
        "sceneSummary": sentence_case(trim_sentence(raw.get("sceneSummary"), 180)) or fallback["sceneSummary"],
        "objects": objects[:6],
        "sequence": sorted(sequence, key=lambda item: item["step"]),
    }
