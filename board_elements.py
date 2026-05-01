from __future__ import annotations

from board_scene_library import scene_object_library_text


def board_element_library_text() -> str:
    return """Excalidraw-style static scene target:
- Build one coherent classroom board scene, not a collage of disconnected items.
- Visualize the meaning of the explanation, not the wording of the answer.
- Use a staged static layout that reveals emphasis beat by beat as the audio progresses.

Renderer contract:
- Use `excalidraw` as the static render mode.
- The payload must use `style: "semantic_scene"` because Parallea renders the static scene on its immersive board runtime.

Return payload shape:
```json
{
  "style": "semantic_scene",
  "title": "short board title",
  "subtitle": "optional short subtitle",
  "objects": [
    {
      "id": "obj_1",
      "kind": "matrix",
      "slot": "center",
      "label": "Image as a matrix",
      "detail": "pixels become numbers"
    }
  ],
  "connectors": [
    {
      "from": "obj_1",
      "to": "obj_2",
      "label": "transforms into"
    }
  ],
  "beats": [
    {
      "id": "beat_1",
      "start_pct": 0.0,
      "end_pct": 0.34,
      "focus": ["obj_1"],
      "caption": "start from the representation"
    }
  ]
}
```

Scene rules:
- Use 1 primary object and at most 2 supporting objects.
- Put the primary meaning object in `center` whenever possible.
- Use `left` and `right` for support views or real-world examples that make the concept intuitive.
- Use each slot at most once.
- Keep captions short and student-facing.
- Use `detail` only for small supporting text under an object.
- Use `connectors` only when a relationship really matters.
- Use 2 to 4 beats, ordered and non-overlapping.
- Beats should progressively reveal or emphasize the objects in the order a good teacher would explain them.
- The board should feel alive through object animation, but the layout should stay calm and readable.
- Real-world reference scenes are allowed when they genuinely carry the meaning, such as a walking child for motion, a balloon escaping a hand for upward force, or a boat on a river for buoyancy and current.

""" + scene_object_library_text() + """

Hard constraints:
- Do not output raw x/y coordinates.
- Do not place many text boxes on the board.
- Do not overlap objects.
- Do not use unsupported object kinds or slot names.
- Use `note_card` only when no richer object fits the meaning.
- Prefer semantic objects like `atom`, `blood_flow`, `lungs`, `matrix`, `neural_net`, `force_arrows`, `walking_child`, `boat_river`, and `cartesian_plane` when they truly match the concept.
- Prefer reusable assets and semantic objects over arbitrary raw drawing instructions."""
