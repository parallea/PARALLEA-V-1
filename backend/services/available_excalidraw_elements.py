from __future__ import annotations

from typing import Any

from board_asset_library import BOARD_ASSETS
from board_scene_library import BOARD_SCENE_OBJECTS, BOARD_SCENE_SLOTS


def _asset_element_id(name: str) -> str:
    return f"asset:{name}"


def _semantic_element_id(name: str) -> str:
    return f"semantic:{name}"


def available_excalidraw_elements() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, item in sorted(BOARD_ASSETS.items()):
        rows.append(
            {
                "id": _asset_element_id(name),
                "kind": "asset",
                "name": name,
                "label": item["label"],
                "description": item["description"],
                "keywords": list(item["keywords"]),
                "defaultMotion": item["motion"],
            }
        )
    for name, item in sorted(BOARD_SCENE_OBJECTS.items()):
        rows.append(
            {
                "id": _semantic_element_id(name),
                "kind": "semantic_object",
                "name": name,
                "label": name.replace("_", " ").title(),
                "description": item["description"],
                "keywords": list(item["keywords"]),
                "allowedSlots": list(BOARD_SCENE_SLOTS.keys()),
            }
        )
    return rows


def excalidraw_element_ids() -> set[str]:
    return {item["id"] for item in available_excalidraw_elements()}


def excalidraw_elements_library_text() -> str:
    rows = ["Available Excalidraw-compatible board library elements:"]
    rows.append("Reusable board assets:")
    for name, item in sorted(BOARD_ASSETS.items()):
        rows.append(
            f'- `asset:{name}`: {item["label"]}. {item["description"]}. Keywords: {", ".join(item["keywords"][:6])}.'
        )
    rows.append("")
    rows.append("Semantic scene objects:")
    for name, item in sorted(BOARD_SCENE_OBJECTS.items()):
        rows.append(
            f'- `semantic:{name}`: {item["description"]}. Slots: {", ".join(BOARD_SCENE_SLOTS.keys())}.'
        )
    rows.append("")
    rows.append("Rules:")
    rows.append("- Use only element ids listed above.")
    rows.append("- Prefer one anchor element and up to two support elements.")
    rows.append("- Excalidraw is the default for conceptual teaching unless graphing or precise math animation genuinely needs Manim.")
    return "\n".join(rows)


def element_by_id(element_id: str) -> dict[str, Any] | None:
    for item in available_excalidraw_elements():
        if item["id"] == element_id:
            return item
    return None


def semantic_object_name(element_id: str) -> str:
    if element_id.startswith("semantic:"):
        return element_id.split(":", 1)[1]
    return ""


def asset_name(element_id: str) -> str:
    if element_id.startswith("asset:"):
        return element_id.split(":", 1)[1]
    return ""
