# Explanation Visual Pipeline Plan

## Applied Strategy

The upgrade was implemented inside the existing FastAPI + HTML runtime instead of introducing a parallel React stack. The current immersive lesson flow stays intact:

`learn.html` -> `/chat` or `/chat-stream` -> `main.py` -> `rag.py` -> `teaching_pipeline.py`

The change was to replace the blueprint-generation internals with a stricter question pipeline.

## New Backend Service Layer

New modules were added under [backend/services](/D:/copy/0%20-%20Copy/backend/services):

- `intent_router.py`
- `explanation_prompt_builder.py`
- `explanation_generator.py`
- `gemini_scene_director.py`
- `available_excalidraw_elements.py`
- `excalidraw_adapter.py`
- `manim_adapter.py`
- `frame_router.py`
- `presentation_sync.py`
- `session_state.py`
- `schema.py`
- `validators.py`
- `question_pipeline.py`

## Runtime Plan

1. Route the learner utterance into `simple_explain`, `brief_explain`, `repeat_previous`, or `visualize`.
2. Read and normalize the persisted `teaching_session_state`.
3. Generate a first-pass explanation with mode-specific prompting.
4. Send the explanation package to Gemini scene director with a strict schema.
5. Validate Gemini output; fall back to deterministic scene generation when needed.
6. Route each frame to Excalidraw or Manim with Excalidraw as the default.
7. Build adapter output per frame.
8. Build synced spoken segments, frame sequence, and visual payload for the existing board runtime.
9. Persist the resulting explanation/visual state back to the session for repeat continuity.
10. Expose pipeline debug state to the frontend.

## Integration Points

- [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py) now calls `build_question_pipeline`.
- [rag.py](/D:/copy/0%20-%20Copy/rag.py) passes `session_state` into the teaching pipeline and returns updated pipeline debug/state.
- [main.py](/D:/copy/0%20-%20Copy/main.py) persists `teaching_session_state` inside the session JSON and returns `pipeline_debug`.
- [learn.html](/D:/copy/0%20-%20Copy/learn.html) now exposes a debug panel and a repeat-previous control while preserving the current board-first teaching UI.

## Visualizer Routing Policy

- Excalidraw is the default.
- Manim is allowed only when graphing, geometry transformation, or equation animation adds instructional value.
- Repeat requests reuse stored frame sequence and visualizer outputs instead of rebuilding a different plan.

## Frontend Scope Decision

The request listed React/TypeScript module names, but this repository does not use a `src/` frontend build. Equivalent functionality was integrated directly into [learn.html](/D:/copy/0%20-%20Copy/learn.html), which is the active immersive learning surface in this codebase.

