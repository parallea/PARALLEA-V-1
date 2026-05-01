from __future__ import annotations

import re

from board_scene_library import suggest_scene_objects


PRIMARY_SLOTS = ["center", "left", "right"]
SUPPORT_SLOTS = ["right", "left", "bottom_right", "bottom_left", "top_right", "top_left"]


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def sentence_case(text: str) -> str:
    text = clean_spaces(text)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def trim_sentence(text: str, limit: int = 140) -> str:
    text = clean_spaces(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip()
    return (cut or text[:limit]).rstrip(".,;: ") + "..."


def split_sentences(text: str, limit: int = 4) -> list[str]:
    parts = [clean_spaces(part) for part in re.split(r"(?<=[.!?])\s+", clean_spaces(text)) if clean_spaces(part)]
    if not parts and clean_spaces(text):
        parts = [sentence_case(text)]
    return [sentence_case(part) for part in parts[:limit]]


def object_label(kind: str, question: str, answer_line: str, focus_text: str) -> str:
    labels = {
        "atom": "Atomic structure",
        "blood_flow": "Blood flow",
        "lungs": "Breathing lungs",
        "neural_net": "Signal layers",
        "matrix": "Matrix view",
        "cartesian_plane": "Graph view",
        "wave": "Signal wave",
        "vector_axes": "Vector view",
        "beaker": "Experiment view",
        "walking_child": "Walking example",
        "balloon_escape": "Balloon motion",
        "boat_river": "Floating boat",
        "force_arrows": "Forces in balance",
        "spring_mass": "Spring motion",
        "pendulum": "Pendulum swing",
        "planet_orbit": "Orbital motion",
        "magnet_field": "Magnetic field",
        "triangle": "Geometry view",
        "process_chain": "Core process",
        "note_card": "Key idea",
    }
    base = labels.get(kind, "Key idea")
    text = focus_text or answer_line or question
    if kind == "note_card":
        return trim_sentence(text, 34)
    return base


def object_detail(kind: str, question: str, answer_line: str, focus_text: str) -> str:
    defaults = {
        "walking_child": "Track motion, contact, and friction on the road.",
        "balloon_escape": "Watch what pulls upward once the hand lets go.",
        "boat_river": "Separate buoyancy, weight, and water current.",
        "force_arrows": "Compare the arrows to see the net force.",
        "spring_mass": "Stretch and restoring force trade places over time.",
        "pendulum": "Energy swaps as the bob swings through the center.",
        "planet_orbit": "Gravity keeps bending the path instead of letting it fly straight.",
        "magnet_field": "Field lines show where the magnetic pull is strongest.",
    }
    if kind == "process_chain":
        return trim_sentence(answer_line or focus_text or question, 58)
    if kind == "note_card":
        return trim_sentence(answer_line or focus_text or question, 70)
    if kind in defaults and not clean_spaces(focus_text):
        return trim_sentence(defaults[kind], 62)
    return trim_sentence(focus_text or answer_line or question, 54)


def build_objects(question: str, answer: str, focus_text: str, supporting: list[str]) -> list[dict]:
    suggested = suggest_scene_objects(question, answer, focus_text, limit=3)
    objects = []
    used_slots = set()
    lines = split_sentences(answer, limit=4)

    for idx, kind in enumerate(suggested):
        slot_pool = PRIMARY_SLOTS if idx == 0 else SUPPORT_SLOTS
        slot = next((candidate for candidate in slot_pool if candidate not in used_slots), None)
        if not slot:
            break
        used_slots.add(slot)
        line = lines[min(idx, len(lines) - 1)] if lines else focus_text or question
        objects.append(
            {
                "id": f"obj_{idx + 1}",
                "kind": kind,
                "slot": slot,
                "label": object_label(kind, question, line, focus_text),
                "detail": object_detail(kind, question, line, supporting[idx - 1] if idx > 0 and idx - 1 < len(supporting) else focus_text),
            }
        )

    if not objects:
        objects.append(
            {
                "id": "obj_1",
                "kind": "note_card",
                "slot": "center",
                "label": "Core idea",
                "detail": trim_sentence(focus_text or answer or question, 72),
            }
        )
    return objects[:3]


def build_connectors(objects: list[dict]) -> list[dict]:
    if len(objects) < 2:
        return []
    primary = objects[0]
    connectors = []
    for idx, obj in enumerate(objects[1:], start=1):
        label = "real-world example" if obj["kind"] in {"walking_child", "balloon_escape", "boat_river"} else "helps explain"
        connectors.append(
            {
                "from": primary["id"],
                "to": obj["id"],
                "label": label if idx == 1 else "supporting view",
            }
        )
    return connectors[:2]


def build_beats(question: str, answer: str, objects: list[dict]) -> list[dict]:
    lines = split_sentences(answer, limit=4)
    if not lines:
        lines = [sentence_case(trim_sentence(question, 80))]
    beats = []
    total = min(max(len(lines), 2), 4)
    for idx in range(total):
        start = round(idx / total, 2)
        end = round((idx + 1) / total, 2)
        focus = [objects[min(idx, len(objects) - 1)]["id"]]
        if idx > 0:
            focus.insert(0, objects[0]["id"])
        if idx == total - 1 and len(objects) > 1:
            focus = [obj["id"] for obj in objects]
        beats.append(
            {
                "id": f"beat_{idx + 1}",
                "start_pct": start,
                "end_pct": end,
                "focus": focus,
                "caption": trim_sentence(lines[idx] if idx < len(lines) else lines[-1], 88),
            }
        )
    beats[-1]["end_pct"] = 1.0
    return beats


def build_semantic_scene_payload(
    title: str,
    question: str,
    answer: str,
    focus_text: str = "",
    supporting: list[str] | None = None,
) -> dict:
    support_lines = [sentence_case(trim_sentence(item, 92)) for item in (supporting or []) if clean_spaces(item)]
    objects = build_objects(question, answer, focus_text, support_lines)
    payload = {
        "style": "semantic_scene",
        "title": trim_sentence(title or "Lesson board", 56),
        "subtitle": trim_sentence(focus_text or answer or question, 88),
        "objects": objects,
        "connectors": build_connectors(objects),
        "beats": build_beats(question, answer, objects),
    }
    return {
        "segments": [
            {
                "id": "semantic_scene_main",
                "title": trim_sentence(title or "Semantic scene", 48),
                "start_pct": 0.0,
                "end_pct": 1.0,
                "kind": "whiteboard",
                "payload": payload,
            }
        ]
    }


def build_blackboard_visual_payload(
    title: str,
    question: str,
    answer: str,
    focus_text: str = "",
    supporting: list[str] | None = None,
) -> dict:
    return build_semantic_scene_payload(
        title=title,
        question=question,
        answer=answer,
        focus_text=focus_text,
        supporting=supporting,
    )
