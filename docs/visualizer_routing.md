# Visualizer Routing

## Policy

Excalidraw is the default visualizer.

Manim is selected only when the frame genuinely benefits from:

- function graphing
- axis-based plots
- geometry transformation
- equation progression
- precise mathematical animation

## Implementation

Routing happens in [backend/services/frame_router.py](/D:/copy/0%20-%20Copy/backend/services/frame_router.py).

The router:

1. respects a learner-forced visualization preference when present
2. otherwise treats `excalidraw` as the default
3. only keeps `manim` when the frame actually looks math/graph/geometry heavy

## Excalidraw Constraints

Allowed Excalidraw-compatible element ids come from [backend/services/available_excalidraw_elements.py](/D:/copy/0%20-%20Copy/backend/services/available_excalidraw_elements.py).

The adapter rejects invented ids and falls back to a minimal valid plan.

## Repeat Behavior

For repeat requests, the router reuses the stored frame sequence and visualizer outputs from the session state instead of rebuilding a different visual plan.

