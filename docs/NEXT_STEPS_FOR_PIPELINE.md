# Next Steps For Pipeline

## Recommended Follow-Ups

1. Add a persisted `lastMaterializedFrameSequence` cache if repeat requests should reuse already-rendered Manim media URLs instead of only reusing the planned payload.
2. Add schema-level snapshot tests for the exact HTTP response contract returned by `/chat` and `/chat-stream`.
3. Move the inline debug panel logic from [learn.html](/D:/copy/0%20-%20Copy/learn.html) into a dedicated JS module if the frontend is later split out of the HTML file.
4. Add adapter-level quality scoring so Excalidraw plans can reject cluttered layouts before they reach the board runtime.
5. Expand the board asset library so conceptual biology/process teaching has more non-math visual anchors without falling back to text-heavy boards.

## Optional Product Enhancements

1. Add a UI-level distinction between `replay answer` and `repeat previous` so learners can see whether the system reused state or just replayed audio.
2. Add a session timeline of prior explanation states for multi-turn concept continuity beyond the last turn only.
3. Add a backend feature flag to switch between deterministic adapter fallback and Gemini-driven adapter planning for evaluation.
