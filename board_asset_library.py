from __future__ import annotations

import re


BOARD_ASSETS = {
    "algorithm": {
        "file": "algorithm.svg",
        "label": "Algorithm stack",
        "category": "machine_learning",
        "description": "stacked algorithm icon for workflow, optimization, or training pipeline topics",
        "keywords": ["algorithm", "pipeline", "workflow", "optimization", "procedure", "training"],
        "motion": "float",
    },
    "autoencoder": {
        "file": "autoencoder.svg",
        "label": "Autoencoder",
        "category": "machine_learning",
        "description": "encoder bottleneck diagram for latent space, compression, or reconstruction topics",
        "keywords": ["autoencoder", "encoder", "decoder", "latent", "compression", "reconstruction"],
        "motion": "pulse",
    },
    "beaker_water": {
        "file": "beaker_water.svg",
        "label": "Beaker",
        "category": "science",
        "description": "beaker icon for chemistry, solution, reaction, or experiment topics",
        "keywords": ["beaker", "chemistry", "solution", "reaction", "lab", "experiment"],
        "motion": "float",
    },
    "cartesian_plane": {
        "file": "cartesian_plane.svg",
        "label": "Cartesian plane",
        "category": "math",
        "description": "coordinate plane for graph, function, trend, or axis-based explanations",
        "keywords": ["graph", "plot", "coordinate", "axes", "function", "curve", "trend", "x", "y"],
        "motion": "drift",
    },
    "integral_curve": {
        "file": "integral_curve.svg",
        "label": "Integral curve",
        "category": "math",
        "description": "area-under-curve sketch for calculus, accumulation, or continuous change",
        "keywords": ["integral", "area", "calculus", "accumulation", "continuous", "curve"],
        "motion": "drift",
    },
    "matrix_grid": {
        "file": "matrix_grid.svg",
        "label": "Matrix grid",
        "category": "math",
        "description": "matrix table for matrix, tensor, array, or linear algebra explanations",
        "keywords": ["matrix", "tensor", "array", "linear algebra", "embedding", "grid"],
        "motion": "pulse",
    },
    "neural_network": {
        "file": "neural_network.svg",
        "label": "Neural network",
        "category": "machine_learning",
        "description": "layered node graph for neural network, inference, or hidden layer explanations",
        "keywords": ["neural", "network", "layer", "inference", "model", "perceptron"],
        "motion": "pulse",
    },
    "round_flask": {
        "file": "round_flask.svg",
        "label": "Round flask",
        "category": "science",
        "description": "round flask icon for chemistry, lab setup, or molecular reaction topics",
        "keywords": ["flask", "chemistry", "lab", "molecule", "reaction"],
        "motion": "float",
    },
    "sigma_sum": {
        "file": "sigma_sum.svg",
        "label": "Sigma sum",
        "category": "math",
        "description": "summation mark for series, aggregation, or repeated addition",
        "keywords": ["sum", "series", "sigma", "aggregate", "aggregation"],
        "motion": "pulse",
    },
    "sine_wave": {
        "file": "sine_wave.svg",
        "label": "Sine wave",
        "category": "math",
        "description": "wave sketch for signal, oscillation, periodic function, or frequency",
        "keywords": ["wave", "signal", "oscillation", "frequency", "periodic", "sine"],
        "motion": "drift",
    },
    "triangle_geometry": {
        "file": "triangle_geometry.svg",
        "label": "Triangle geometry",
        "category": "math",
        "description": "triangle diagram for geometry, angle, or trigonometry topics",
        "keywords": ["triangle", "geometry", "angle", "trigonometry", "distance"],
        "motion": "drift",
    },
    "vector_axes": {
        "file": "vector_axes.svg",
        "label": "Vector axes",
        "category": "math",
        "description": "vector on axes for direction, magnitude, basis, or linear algebra topics",
        "keywords": ["vector", "direction", "magnitude", "basis", "projection", "force"],
        "motion": "drift",
    },
}

EXCALIDRAW_ASSET_LIBRARY = BOARD_ASSETS


def asset_names() -> list[str]:
    return sorted(BOARD_ASSETS.keys())


def is_valid_asset_name(name: str) -> bool:
    return name in BOARD_ASSETS


def asset_file(name: str) -> str:
    return BOARD_ASSETS[name]["file"]


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_+-]+", str(text or "").lower())


def board_asset_library_text() -> str:
    rows = []
    for name in asset_names():
        item = BOARD_ASSETS[name]
        rows.append(
            f'- `{name}`: {item["description"]}. Default motion: `{item["motion"]}`.'
        )
    return "\n".join(rows)


def excalidraw_asset_library_text() -> str:
    rows = ["Available reusable Excalidraw-style assets:"]
    for name in asset_names():
        item = BOARD_ASSETS[name]
        keywords = ", ".join(item["keywords"][:6])
        rows.append(
            f'- `{name}`: {item["label"]}. Category: `{item["category"]}`. '
            f'{item["description"]}. Keywords: {keywords}. Default motion: `{item["motion"]}`.'
        )
    return "\n".join(rows)


def suggest_board_assets(question: str, answer: str, limit: int = 2) -> list[str]:
    tokens = set(tokenize(f"{question} {answer}"))
    scored = []
    for name, item in BOARD_ASSETS.items():
        score = sum(2 if keyword in tokens else 0 for keyword in item["keywords"])
        score += sum(1 for keyword in item["keywords"] if keyword in f"{question} {answer}".lower())
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [name for _, name in scored[:limit]]


def suggest_excalidraw_assets(question: str, answer: str, limit: int = 2) -> list[str]:
    return suggest_board_assets(question, answer, limit=limit)
