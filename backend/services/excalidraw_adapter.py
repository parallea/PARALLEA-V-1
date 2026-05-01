from __future__ import annotations

import logging
import os
from typing import Any

from board_asset_library import BOARD_ASSETS
from config import GEMINI_API_KEY
from gemini_service import build_gemini_client, generate_json_with_retry
from model_routing import resolve_gemini_model_config

from .available_excalidraw_elements import (
    asset_name,
    element_by_id,
    excalidraw_element_ids,
    excalidraw_elements_library_text,
    semantic_object_name,
)
from .schema import excalidraw_frame_plan_schema
from .validators import clean_spaces, normalize_excalidraw_frame_plan, parse_json_blob, sentence_case, trim_sentence


logger = logging.getLogger("parallea.excalidraw-adapter")
REMOTE_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if GEMINI_API_KEY else "0") == "1"
EXCALIDRAW_ADAPTER_MODEL = resolve_gemini_model_config(
    "PARALLEA_GEMINI_EXCALIDRAW_ADAPTER_MODEL",
    fallback_envs=["PARALLEA_GEMINI_FRAME_MODEL", "PARALLEA_GEMINI_TEACHING_MODEL"],
    default="gemini-2.5-flash",
    label="excalidraw-adapter",
)["model"]
gemini_client = build_gemini_client(GEMINI_API_KEY, enabled=REMOTE_ENABLED)


POSITION_TO_SLOT = {
    "center": "center",
    "left": "left",
    "right": "right",
    "top": "top_left",
    "top_left": "top_left",
    "top_right": "top_right",
    "bottom": "bottom_right",
    "bottom_left": "bottom_left",
    "bottom_right": "bottom_right",
}

POSITION_TO_COORDS = {
    "center": (370, 170, 300, 110),
    "left": (66, 176, 220, 96),
    "right": (520, 176, 220, 96),
    "top": (248, 88, 260, 70),
    "top_left": (68, 88, 200, 66),
    "top_right": (544, 88, 180, 66),
    "bottom": (238, 336, 300, 76),
    "bottom_left": (72, 332, 210, 72),
    "bottom_right": (522, 332, 210, 72),
}


def _heuristic_plan(frame: dict[str, Any]) -> dict[str, Any]:
    elements_needed = [clean_spaces(item) for item in (frame.get("elementsNeeded") or []) if clean_spaces(item)]
    if not elements_needed:
        elements_needed = ["semantic:note_card"]
    ordered_positions = ["center", "left", "right"]
    elements_to_use = []
    for index, element_id in enumerate(elements_needed[:3]):
        element = element_by_id(element_id) or {}
        elements_to_use.append(
            {
                "assetId": element_id,
                "label": trim_sentence(element.get("label") or frame.get("visualGoal"), 48),
                "positionHint": ordered_positions[index] if index < len(ordered_positions) else "right",
                "purpose": sentence_case(trim_sentence(frame.get("visualGoal") if index == 0 else frame.get("sceneDescription"), 120)),
            }
        )
    text_labels = [{"text": trim_sentence(frame.get("visualGoal"), 120), "positionHint": "top"}]
    for formula in (frame.get("formulae") or [])[:2]:
        text_labels.append({"text": trim_sentence(formula, 120), "positionHint": "bottom"})
    if clean_spaces(frame.get("analogy")):
        text_labels.append({"text": trim_sentence(frame.get("analogy"), 120), "positionHint": "bottom_left"})
    arrows = []
    if len(elements_to_use) >= 2:
        arrows.append({"from": elements_to_use[0]["assetId"], "to": elements_to_use[1]["assetId"], "label": trim_sentence(frame.get("visualGoal"), 60)})
    sequence = []
    for step, element in enumerate(elements_to_use, start=1):
        sequence.append({"step": step, "action": "place_asset", "targetIds": [element["assetId"]]})
    text_target = [item["assetId"] for item in elements_to_use[:1]] or ["title"]
    sequence.append({"step": len(sequence) + 1, "action": "show_text", "targetIds": text_target})
    if arrows:
        sequence.append({"step": len(sequence) + 1, "action": "draw_arrow", "targetIds": [arrows[0]["from"], arrows[0]["to"]]})
    sequence.append({"step": len(sequence) + 1, "action": "highlight", "targetIds": text_target})
    return {
        "frameId": clean_spaces(frame.get("id")) or "frame_1",
        "title": trim_sentence(frame.get("sceneDescription") or frame.get("visualGoal"), 72),
        "elementsToUse": elements_to_use,
        "textLabels": text_labels[:4],
        "arrows": arrows[:2],
        "sequence": sequence,
    }


def build_excalidraw_adapter_prompt(frame: dict[str, Any], context: str = "") -> str:
    return f"""
You are Parallea's Excalidraw frame adapter.
Return valid JSON only. Do not use markdown fences.

Rules:
- use only element ids from the provided library
- keep the frame minimal and well spaced
- place the anchor object first
- add supporting elements only when they help understanding
- use arrows only for meaningful relationships
- labels should be short and student-facing

Frame:
{frame}

Context:
{trim_sentence(context, 800) or "No extra context."}

Available element library:
{excalidraw_elements_library_text()}
""".strip()


async def build_excalidraw_frame_plan(frame: dict[str, Any], context: str = "") -> dict[str, Any]:
    fallback = _heuristic_plan(frame)
    if not gemini_client:
        return fallback
    try:
        raw = await generate_json_with_retry(
            gemini_client,
            model=EXCALIDRAW_ADAPTER_MODEL,
            prompt=build_excalidraw_adapter_prompt(frame, context=context),
            system_instruction="Return valid JSON only. Do not use markdown fences.",
            response_schema=excalidraw_frame_plan_schema(),
            logger=logger,
            operation=f"excalidraw-adapter:{clean_spaces(frame.get('id')) or 'frame'}",
            temperature=0.2,
            max_output_tokens=1200,
        )
        return normalize_excalidraw_frame_plan(
            parse_json_blob(raw),
            fallback=fallback,
            allowed_element_ids=excalidraw_element_ids(),
        )
    except Exception as exc:
        logger.exception("excalidraw-adapter failed frame=%s error=%s", frame.get("id"), exc)
        return fallback


def _text_label_to_element(text_label: dict[str, Any], index: int) -> dict[str, Any]:
    x, y, w, _ = POSITION_TO_COORDS.get(clean_spaces(text_label.get("positionHint")).lower(), POSITION_TO_COORDS["bottom"])
    return {
        "kind": "text",
        "text": trim_sentence(text_label.get("text"), 120),
        "x": x,
        "y": y,
        "size": 18 if index == 0 else 15,
        "w": w,
    }


def _build_generic_elements_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    elements = []
    for index, item in enumerate(plan.get("textLabels") or [], start=1):
        elements.append(_text_label_to_element(item, index))
    if elements:
        elements.append({"kind": "underline", "x": 58, "y": 96, "w": 640})
    for arrow in plan.get("arrows") or []:
        from_pos = POSITION_TO_COORDS.get(clean_spaces(next((element.get("positionHint") for element in plan.get("elementsToUse", []) if element.get("assetId") == arrow.get("from")), "center")).lower(), POSITION_TO_COORDS["center"])
        to_pos = POSITION_TO_COORDS.get(clean_spaces(next((element.get("positionHint") for element in plan.get("elementsToUse", []) if element.get("assetId") == arrow.get("to")), "right")).lower(), POSITION_TO_COORDS["right"])
        elements.append(
            {
                "kind": "arrow",
                "from": [from_pos[0] + (from_pos[2] / 2), from_pos[1] + 60],
                "to": [to_pos[0] + (to_pos[2] / 2), to_pos[1] + 60],
                "label": trim_sentence(arrow.get("label"), 60),
            }
        )
    return elements[:8]


def excalidraw_plan_to_renderer_payload(plan: dict[str, Any], frame: dict[str, Any]) -> dict[str, Any]:
    objects = []
    connectors = []
    assets = []
    object_ids_by_element: dict[str, str] = {}
    for index, item in enumerate(plan.get("elementsToUse") or [], start=1):
        element_id = clean_spaces(item.get("assetId"))
        position_hint = clean_spaces(item.get("positionHint")).lower() or ("center" if index == 1 else "left")
        slot = POSITION_TO_SLOT.get(position_hint, "center" if index == 1 else "right")
        semantic_name = semantic_object_name(element_id)
        if semantic_name:
            object_id = f"obj_{index}"
            object_ids_by_element[element_id] = object_id
            objects.append(
                {
                    "id": object_id,
                    "kind": semantic_name,
                    "slot": slot,
                    "label": trim_sentence(item.get("label") or frame.get("visualGoal"), 42),
                    "detail": trim_sentence(item.get("purpose") or frame.get("sceneDescription"), 80),
                }
            )
            continue
        resolved_asset_name = asset_name(element_id)
        if resolved_asset_name and resolved_asset_name in BOARD_ASSETS:
            assets.append(
                {
                    "id": f"asset_{index}",
                    "name": resolved_asset_name,
                    "url": f"/board-assets/{BOARD_ASSETS[resolved_asset_name]['file']}",
                    "slot": slot,
                    "label": trim_sentence(item.get("label") or BOARD_ASSETS[resolved_asset_name]["label"], 32),
                    "motion": BOARD_ASSETS[resolved_asset_name]["motion"],
                }
            )
    if not objects:
        objects.append(
            {
                "id": "obj_1",
                "kind": "note_card",
                "slot": "center",
                "label": trim_sentence(frame.get("sceneDescription") or frame.get("visualGoal"), 42) or "Core idea",
                "detail": trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription"), 82),
            }
        )
    for arrow in plan.get("arrows") or []:
        from_id = object_ids_by_element.get(clean_spaces(arrow.get("from")))
        to_id = object_ids_by_element.get(clean_spaces(arrow.get("to")))
        if from_id and to_id and from_id != to_id:
            connectors.append({"from": from_id, "to": to_id, "label": trim_sentence(arrow.get("label"), 48) or "connects"})
    beats = []
    active_ids = [item["id"] for item in objects]
    for index, step in enumerate(plan.get("sequence") or [], start=1):
        focus = []
        for target in step.get("targetIds") or []:
            mapped = object_ids_by_element.get(clean_spaces(target))
            if mapped and mapped not in focus:
                focus.append(mapped)
        if not focus:
            focus = active_ids[:1]
        beats.append(
            {
                "id": f"beat_{index}",
                "start_pct": round((index - 1) / max(1, len(plan.get("sequence") or [1])), 2),
                "end_pct": round(index / max(1, len(plan.get("sequence") or [1])), 2),
                "focus": focus[:3],
                "caption": sentence_case(trim_sentence(frame.get("visualNotes", ["Follow the scene."])[0], 88)),
            }
        )
    if beats:
        beats[-1]["end_pct"] = 1.0
    payload = {
        "style": "semantic_scene",
        "title": trim_sentence(plan.get("title") or frame.get("sceneDescription"), 56),
        "subtitle": trim_sentence(frame.get("visualGoal") or frame.get("sceneDescription"), 92),
        "objects": objects[:3],
        "connectors": connectors[:3],
        "beats": beats[:4],
        "assets": assets[:3],
        "elements": _build_generic_elements_from_plan(plan),
        "framePlan": plan,
    }
    return payload
