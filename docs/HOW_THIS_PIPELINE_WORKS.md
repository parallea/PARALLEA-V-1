# How This Pipeline Works

## End-to-End Flow

1. The learner asks a question in the immersive lesson UI.
2. [main.py](/D:/copy/0%20-%20Copy/main.py) loads the persisted session and passes `teaching_session_state` downstream.
3. [rag.py](/D:/copy/0%20-%20Copy/rag.py) calls [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py).
4. [teaching_pipeline.py](/D:/copy/0%20-%20Copy/teaching_pipeline.py) now delegates blueprint construction to [backend/services/question_pipeline.py](/D:/copy/0%20-%20Copy/backend/services/question_pipeline.py).
5. The question pipeline:
   - routes intent
   - generates or reuses the explanation
   - directs scenes through Gemini with strict JSON
   - routes frames to Excalidraw or Manim
   - builds synced presentation output
   - persists the reusable state back into the session
6. The existing board runtime in [learn.html](/D:/copy/0%20-%20Copy/learn.html) consumes the resulting `visual_payload`, `teaching_segments`, and `frame_sequence`.
7. The frontend audio player keeps board frames synchronized to the spoken explanation.

## Repeat Path

When the learner says `Can you please repeat it`:

- the intent router classifies it as `repeat_previous`
- the explanation generator reuses the last explanation if available
- the scene director reuses the last scene output if available
- the frame router reuses the last frame sequence and visualizer outputs if available

This preserves explanation continuity and avoids random re-generation.

## Debug Path

The backend now returns `pipeline_debug`.

The immersive UI shows:

- detected intent
- normalized question
- first-pass explanation
- Gemini scene JSON
- visualizer chosen per frame
- selected Excalidraw elements
- repeat-state reuse flags

