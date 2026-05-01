from __future__ import annotations

from typing import Any

from .excalidraw_adapter import build_excalidraw_frame_plan, excalidraw_plan_to_renderer_payload
from .manim_adapter import build_manim_frame_plan, manim_plan_to_renderer_payload
from .validators import clean_spaces, sentence_case, trim_sentence


def _visualizer_priority(frame: dict[str, Any], preferred_visualization: str = "") -> str:
    forced = clean_spaces(preferred_visualization).lower()
    if forced in {"excalidraw", "manim"}:
        return forced
    requested = clean_spaces(frame.get("visualizer")).lower()
    if requested == "manim":
        text = clean_spaces(frame.get("sceneDescription")) + " " + clean_spaces(frame.get("visualGoal"))
        if frame.get("functionsToDraw") or any(term in text.lower() for term in ["graph", "plot", "equation", "geometry", "transform", "axes"]):
            return "manim"
    return "excalidraw"


async def route_frames(
    *,
    question: str,
    scene_output: dict[str, Any],
    context: str,
    preferred_visualization: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frame_sequence: list[dict[str, Any]] = []
    visualizer_outputs: list[dict[str, Any]] = []
    spoken_segments = [item for item in (scene_output.get("spokenAnswerSegments") or []) if isinstance(item, dict)]
    for index, frame in enumerate(scene_output.get("frames") or [], start=1):
        if not isinstance(frame, dict):
            continue
        render_mode = _visualizer_priority(frame, preferred_visualization=preferred_visualization)
        segment_ref = clean_spaces((spoken_segments[index - 1] if index - 1 < len(spoken_segments) else {}).get("id")) or clean_spaces(frame.get("id")) or f"segment_{index}"
        if render_mode == "manim":
            plan = await build_manim_frame_plan(frame, context=context)
            renderer_payload = manim_plan_to_renderer_payload(plan, frame)
            visualizer_output = {"frameId": clean_spaces(frame.get("id")) or f"frame_{index}", "visualizer": "manim", "plan": plan}
            selected_asset_ids: list[str] = []
        else:
            plan = await build_excalidraw_frame_plan(frame, context=context)
            renderer_payload = excalidraw_plan_to_renderer_payload(plan, frame)
            visualizer_output = {"frameId": clean_spaces(frame.get("id")) or f"frame_{index}", "visualizer": "excalidraw", "plan": plan}
            selected_asset_ids = [clean_spaces(item.get("assetId")) for item in (plan.get("elementsToUse") or []) if clean_spaces(item.get("assetId"))]
        visualizer_outputs.append(visualizer_output)
        frame_sequence.append(
            {
                "frame_number": index,
                "segment_id": segment_ref,
                "speech_segment_ref": segment_ref,
                "title": trim_sentence(frame.get("sceneDescription") or frame.get("visualGoal"), 56),
                "render_mode": render_mode,
                "reason": sentence_case(trim_sentence(f"Use {render_mode} for this frame because it best matches the teaching move.", 180)),
                "scene_goal": sentence_case(trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription"), 220)),
                "notes_for_sync": sentence_case(trim_sentence("Keep the visual transition synchronized to the spoken segment.", 140)),
                "layout_notes": sentence_case(trim_sentence("Minimal, classroom-first layout with one anchor and limited supporting elements.", 160)),
                "object_actions": [sentence_case(trim_sentence(note, 140)) for note in (frame.get("visualNotes") or []) if clean_spaces(note)][:4],
                "selected_asset_ids": selected_asset_ids[:3],
                "visual_assets": (renderer_payload.get("assets") or [])[:3] if isinstance(renderer_payload, dict) else [],
                "renderer_payload": renderer_payload,
                "payload": renderer_payload,
                "timeline_start": clean_spaces(frame.get("timelineStart")),
                "timeline_end": clean_spaces(frame.get("timelineEnd")),
                "formulae": [trim_sentence(item, 120) for item in (frame.get("formulae") or []) if clean_spaces(item)],
                "functions": [item for item in (frame.get("functionsToShow") or []) if isinstance(item, dict)],
                "visualizer_output": visualizer_output,
                "fallback_mode": "excalidraw" if render_mode == "manim" else "manim",
                "fallback": {},
                "visual_pipeline_path": f"question_pipeline_{render_mode}",
                "debug": {"question": trim_sentence(question, 220), "frame": frame},
            }
        )
    return frame_sequence, visualizer_outputs
