from __future__ import annotations

import re


BOARD_SCENE_OBJECTS = {
    "atom": {
        "description": "atom model with nucleus and revolving electrons",
        "keywords": ["atom", "electron", "proton", "neutron", "orbit", "energy level"],
    },
    "blood_flow": {
        "description": "blood vessel with animated red cells moving through it",
        "keywords": ["blood", "vein", "artery", "circulation", "flow", "oxygen"],
    },
    "lungs": {
        "description": "breathing lungs with expanding contraction motion and airflow",
        "keywords": ["lung", "lungs", "breathing", "respiration", "alveoli", "oxygen"],
    },
    "neural_net": {
        "description": "layered neural network with signal pulses moving across connections",
        "keywords": ["neural network", "layer", "inference", "perceptron", "model", "hidden layer"],
    },
    "matrix": {
        "description": "matrix grid with active cells lighting up",
        "keywords": ["matrix", "tensor", "array", "linear algebra", "pixels", "embedding"],
    },
    "cartesian_plane": {
        "description": "coordinate plane with animated curve or traveling point",
        "keywords": ["graph", "plot", "curve", "function", "coordinate", "axis", "axes"],
    },
    "wave": {
        "description": "moving wave for signal, frequency, or oscillation",
        "keywords": ["wave", "signal", "frequency", "periodic", "oscillation", "sine"],
    },
    "vector_axes": {
        "description": "axes with an animated vector showing direction and magnitude",
        "keywords": ["vector", "direction", "magnitude", "basis", "projection", "force"],
    },
    "beaker": {
        "description": "beaker with liquid and bubbles for experiment or chemistry concepts",
        "keywords": ["beaker", "solution", "reaction", "chemistry", "lab", "mixture"],
    },
    "walking_child": {
        "description": "child walking along a road with motion cues",
        "keywords": ["child", "walk", "walking", "road", "motion", "speed", "friction"],
    },
    "balloon_escape": {
        "description": "balloon slipping away with string and upward motion",
        "keywords": ["balloon", "grip", "slip", "released", "helium", "upward", "lift", "drift away"],
    },
    "boat_river": {
        "description": "boat floating on a river with water current underneath",
        "keywords": ["boat", "river", "float", "floating", "buoyancy", "water", "current"],
    },
    "force_arrows": {
        "description": "central body with opposing or unbalanced force arrows",
        "keywords": ["force", "push", "pull", "gravity", "drag", "lift", "weight", "net force"],
    },
    "spring_mass": {
        "description": "mass on a spring showing stretch, compression, and restoring motion",
        "keywords": ["spring", "elastic", "hooke", "stretch", "compression", "restoring force", "oscillation"],
    },
    "pendulum": {
        "description": "swinging pendulum showing periodic motion",
        "keywords": ["pendulum", "swing", "oscillation", "period", "motion"],
    },
    "planet_orbit": {
        "description": "planet moving around a central body on an orbit",
        "keywords": ["planet", "orbit", "solar", "gravity", "satellite", "revolve"],
    },
    "magnet_field": {
        "description": "bar magnet with moving field lines and poles",
        "keywords": ["magnet", "magnetic", "field", "north pole", "south pole", "electromagnet"],
    },
    "triangle": {
        "description": "geometry triangle with angle emphasis",
        "keywords": ["triangle", "geometry", "trigonometry", "angle", "distance"],
    },
    "process_chain": {
        "description": "three-step process chain with traveling emphasis",
        "keywords": ["process", "steps", "pipeline", "algorithm", "workflow", "sequence"],
    },
    "note_card": {
        "description": "minimal concept card when no richer visual object fits",
        "keywords": [],
    },
}


BOARD_SCENE_SLOTS = {
    "center": "main concept area in the middle of the board",
    "left": "support object on the left side",
    "right": "support object on the right side",
    "bottom_left": "support object in the lower left area",
    "bottom_right": "support object in the lower right area",
    "top_left": "small support object in the upper left area",
    "top_right": "small support object in the upper right area",
}


def is_valid_scene_object(kind: str) -> bool:
    return kind in BOARD_SCENE_OBJECTS


def is_valid_scene_slot(slot: str) -> bool:
    return slot in BOARD_SCENE_SLOTS


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_+-]+", str(text or "").lower())


def suggest_scene_objects(question: str, answer: str, context: str = "", limit: int = 3) -> list[str]:
    tokens = set(tokenize(f"{question} {answer} {context}"))
    scored = []
    haystack = f"{question} {answer} {context}".lower()
    for name, item in BOARD_SCENE_OBJECTS.items():
        score = 0
        matched = 0
        for keyword in item["keywords"]:
            keyword_tokens = tokenize(keyword)
            if keyword_tokens and all(token in tokens for token in keyword_tokens):
                score += 3
                matched += 1
                continue
            if keyword in tokens:
                score += 2
                matched += 1
                continue
            if keyword in haystack:
                score += 1
                matched += 1
        if matched >= 2:
            score += 1
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda row: (-row[0], row[1]))
    picks = [name for _, name in scored[:limit]]
    if not picks:
        picks = ["process_chain", "note_card"]
    elif len(picks) == 1 and picks[0] != "note_card":
        picks.append("note_card")
    return picks[:limit]


def scene_object_library_text() -> str:
    rows = ["Available semantic scene objects:"]
    for name, item in BOARD_SCENE_OBJECTS.items():
        rows.append(f'- `{name}`: {item["description"]}.')
    rows.append("")
    rows.append("Available scene slots:")
    for name, description in BOARD_SCENE_SLOTS.items():
        rows.append(f'- `{name}`: {description}.')
    return "\n".join(rows)
