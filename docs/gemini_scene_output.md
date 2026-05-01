# Gemini Scene Output

## Role

Gemini is now used as a strict scene director, not as a free-form visual writer.

It must return JSON that:

- segments the answer into timed spoken parts
- extracts formulas/functions when needed
- plans frame-by-frame scenes
- chooses `excalidraw` or `manim` per frame

## Validation Rules

The response is normalized and validated in [backend/services/validators.py](/D:/copy/0%20-%20Copy/backend/services/validators.py).

Validation enforces:

- valid mode names
- valid spoken segment purposes
- normalized HH:MM:SS timing
- valid visualizer names
- allowed Excalidraw element ids only
- fallback generation when Gemini output is malformed

## Fallback Behavior

If Gemini is unavailable or returns invalid JSON:

- the system falls back to deterministic sentence splitting
- the router still defaults to Excalidraw for conceptual explanations
- graph/function/equation heavy frames can still route to Manim
- the pipeline still produces synchronized spoken segments and visual frames

