# Explanation Visual Pipeline Audit

## 2026-05 Active Immersive Pipeline Audit

The active teacher-persona-first UX is now the backend student session flow, not the older `/chat` board route:

1. Uploaded topic playback starts in `backend/services/session_manager.py` via `set_topic()` -> `_start_uploaded_video_part()` -> `_video_part_ready_envelope()`. This returns the original uploaded teacher video part as the primary content.
2. After the video part ends, `mark_video_part_ended()` asks `Is there anything in this part that you didn't understand?`.
3. Student doubts are handled by `_clarify_current_roadmap_part()`, which calls `generate_teaching_response_with_visuals(mode="video_context_clarification")`.
4. Missing-topic confirmation is handled by `persona_only_confirmation`; after yes, `_answer_persona_only()` calls `generate_teaching_response_with_visuals(mode="persona_only_teaching")`.
5. `generate_teaching_response_with_visuals()` in `backend/services/answer_service.py` is the central combined speech + visual route. It uses `PARALLEA_TEACHING_PIPELINE_PROVIDER` and `PARALLEA_TEACHING_PIPELINE_MODEL`, logs `[teaching-pipeline] provider=... model=... mode=...`, and returns timestamped speech, timestamped Manim visual segments, and Manim code.
6. `_render_teaching_visual()` renders that Manim code through `backend/visuals/manim_renderer.py` / `manim_renderer.py` and returns a frontend-safe `/generated/manim/.../scene.mp4?v=...` URL.
7. `student-learn.js` renders the returned Manim video in the focused clarification modal and tracks best-effort sync states: `waiting_for_visual`, `playing_synced`, `playing_speech_first`, and `visual_failed`.

The board/tldraw/static visual paths remain in older/dev code (`main.py`, `rag.py`, `teaching_pipeline.py`) but are not the primary visual route for `video_context_clarification` or `persona_only_teaching`. The active combined route logs `[visual-routing] primary=manim board=false` and `[visual-routing] board pipeline skipped because manim is primary`.

## Restored Working Manim Contract

The current project also preserves the previous working `/chat-stream` Manim contract:

1. `learn.html` posts to `/chat-stream`.
2. `main.py` streams `first_text`, `plan`, `audio_pending`, `segment`, and `done` events.
3. `rag.py` sends source transcript context when available, or classroom/persona-only context when a teacher video is not available.
4. `teaching_pipeline.py` delegates to `backend/services/question_pipeline.py`.
5. `question_pipeline.py` calls `backend/services/openai_manim_pipeline.py` for one OpenAI-shaped response containing speech segments plus full Manim Python files.
6. OpenAI direct code uses `class ParalleaGeneratedScene(Scene):`; renderer adapters also accept the newer `GeneratedScene` class for current clarification routes.
7. `manim_renderer.py` renders to `data/renders/manim/<hash>.mp4` by default and returns `/rendered-scenes/manim/<hash>.mp4?v=<mtime>`.
8. `learn.html` accepts `media_url`, `video_url`, `public_url`, and nested render-output URL fields before syncing the muted Manim clip to segment audio.

The focused smoke test is:

```powershell
python -m backend.scripts.test_old_manim_pipeline_contract
```

## Stack Summary

PARALLEA is currently a FastAPI application with HTML/JS frontends, not a React/`src/` build. The immersive learning experience is centered in [main.py](/D:/copy/0%20-%20Copy/main.py), [rag.py](/D:/copy/0%20-%20Copy/rag.py), [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py), and [learn.html](/D:/copy/0%20-%20Copy/learn.html).

## Current Question -> Answer Flow

1. `learn.html` sends `/chat` or `/chat-stream`.
2. [main.py](/D:/copy/0%20-%20Copy/main.py) parses the request, loads the session, and builds teaching request context.
3. For lesson mode it calls `get_lesson_teacher_response_async` in [rag.py](/D:/copy/0%20-%20Copy/rag.py).
4. For video mode it calls `get_teaching_response_async` or `stream_teaching_blueprint_async` in [rag.py](/D:/copy/0%20-%20Copy/rag.py).
5. [rag.py](/D:/copy/0%20-%20Copy/rag.py) delegates to [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py) to build structured teaching output.
6. The frontend plays returned audio and visual segments in sequence.

## Current Speech / TTS Flow

- TTS lives in [voice.py](/D:/copy/0%20-%20Copy/voice.py).
- `synthesize_to_file` uses `edge-tts`.
- `speak_text` and `speak_segments` generate full-answer or per-segment audio files in `data/audio`.
- `main.py` queues audio jobs for streamed segment playback and serves audio via `/audio-response/{name}`.

## Current Lesson / Video Flow

- Lesson/video requests are handled in [main.py](/D:/copy/0%20-%20Copy/main.py).
- Video mode can replay a relevant source clip before the explanation.
- `learn.html` plays the clip bridge first, then drives the board with the synced audio/visual segments.
- `rag.py` already supports lesson mode, video-context mode, and direct teaching mode.

## Current Board / Visualization Flow

- The immersive board runtime is in [learn.html](/D:/copy/0%20-%20Copy/learn.html).
- It supports:
  - semantic whiteboard scenes
  - generic whiteboard element payloads
  - Mermaid
  - Chart.js
  - Manim video playback
- Blackboard scene generation helpers already exist in:
  - [blackboard_visuals.py](/D:/copy/0%20-%20Copy/blackboard_visuals.py)
  - [board_asset_library.py](/D:/copy/0%20-%20Copy/board_asset_library.py)
  - [board_scene_library.py](/D:/copy/0%20-%20Copy/board_scene_library.py)
  - [board_elements.py](/D:/copy/0%20-%20Copy/board_elements.py)
- Manim rendering already exists in:
  - [manim_renderer.py](/D:/copy/0%20-%20Copy/manim_renderer.py)
  - [backend/visuals/manim_renderer.py](/D:/copy/0%20-%20Copy/backend/visuals/manim_renderer.py)

## Current State Management

- Session state lives in `_sessions` in [main.py](/D:/copy/0%20-%20Copy/main.py).
- Sessions are persisted to `data/sessions/*.json`.
- Existing session data already stores:
  - conversation history
  - notes
  - transcript log
  - avatar/voice config
  - teaching loop metadata
- This was the correct place to persist explanation/visual continuity for repeat behavior.

## Where Gemini Is Currently Called

- [gemini_service.py](/D:/copy/0%20-%20Copy/gemini_service.py) wraps Gemini JSON calls.
- Existing Gemini usage was spread across:
  - [rag.py](/D:/copy/0%20-%20Copy/rag.py) teacher responses
  - [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py) segmentation and frame planning
  - [backend/visuals/visual_planner.py](/D:/copy/0%20-%20Copy/backend/visuals/visual_planner.py) storyboard generation
- The old unified visual path was still effectively Manim/storyboard-first, which conflicted with the product requirement that Excalidraw be the default visualizer for most concepts.

## Excalidraw / Manim Presence

- Excalidraw-compatible board rendering already existed through the semantic whiteboard runtime in [learn.html](/D:/copy/0%20-%20Copy/learn.html).
- Manim already existed as a first-class renderer with generated scene source and video output.
- The missing piece was a strict explanation-to-scene-to-adapter pipeline that chooses between them per frame.

## Reusable Assets / Libraries

- Static SVG assets live in `board_assets/`.
- Asset metadata lives in [board_asset_library.py](/D:/copy/0%20-%20Copy/board_asset_library.py).
- Semantic board object library lives in [board_scene_library.py](/D:/copy/0%20-%20Copy/board_scene_library.py).
- Excalidraw renderer guidance lives in [board_elements.py](/D:/copy/0%20-%20Copy/board_elements.py).

## Where Previous Explanation State Can Be Stored

- The correct storage location is the persisted session object in [main.py](/D:/copy/0%20-%20Copy/main.py).
- This now supports `teaching_session_state` so repeat requests can reuse:
  - last explanation
  - last spoken segments
  - last formulas/functions
  - last frame plans
  - last visualizer outputs
  - last timestamps

## Audit Conclusion

The repository already had the right runtime pieces for TTS, clip bridging, board playback, and Manim rendering. The main gaps were:

- no strict intent router for explain/brief/repeat/visualize
- no dedicated teaching session memory for explanation/visual continuity
- no strict Gemini scene-output contract for timed spoken segments and per-frame visualizer choice
- no Excalidraw-first routing policy
- no debug surface exposing the pipeline state in the immersive UI
