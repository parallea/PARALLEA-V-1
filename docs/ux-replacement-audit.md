# UX Replacement Audit

Date: 2026-04-30

## Old Routes And Components Found

- `/` previously showed a video-first landing page with public upload, video library, and links into `/player`.
- `/player` and `player.html` were the YouTube-like lesson player entry points.
- `/avatar-select` and `avatar-select.html` were part of the old video-to-avatar flow.
- `/learn` and `learn.html` accepted video-first immersive links and exposed old video context behavior.
- `/videos`, `/video-meta/{video_id}`, and `POST /upload` supported the public video library and public upload flow.
- `learn.html`, `student-learn.js`, `main.py`, and legacy chat endpoints contained the old `use_video_context` internals.

## Removed Or Hidden

- The old homepage UI was replaced with a teacher-persona-first homepage.
- The public upload modal and public video library are no longer visible.
- The student-facing flow no longer shows a video grid or a video context toggle.
- Legacy public `POST /upload` now returns `410` and tells callers to use `/teacher/upload`.

## Redirected

- `/player` and `/player.html` redirect to the persona-first product home for the current role.
- `/avatar-select` and `/avatar-select.html` redirect to the persona-first product home for the current role.
- `/learn` and `/learn.html` redirect to `/student/personas`, or to `/student/learn/{personaId}` when an old `?video=` link can be mapped.
- `/watch/{video_id}` redirects away from the old video-first watch flow.
- `/videos` and `/video-meta/{video_id}` redirect away from public video browsing.
- `GET /upload` redirects teachers to `/teacher/upload`.

## Preserved

- Video file streaming remains available at `/video/{video_id}` for backend/media use.
- Thumbnail serving remains available at `/thumbnail/{video_id}`.
- Microphone transcription remains available at `/transcribe-question`.
- Legacy `/greet`, `/chat`, `/chat-stream`, and `/signal` remain so existing lesson/video processing logic is not deleted.
- The Manim health and rendering pipeline remains unchanged: `/health/manim`, `manim_renderer.py`, `backend/services/openai_manim_pipeline.py`, and `backend/visuals/*`.
- Teacher upload uses the new `/api/teacher/videos/upload` endpoint and `backend/services/persona_pipeline.py`.

## New Routes

- `/auth/login`
- `/auth/signup`
- `/teacher/dashboard`
- `/teacher/upload`
- `/teacher/videos`
- `/teacher/videos/{video_id}`
- `/teacher/roadmaps`
- `/student/personas`
- `/student/learn/{personaId}`

## How To Test Persona Browse

1. Install dependencies: `pip install -r requirements.txt`.
2. Start the app: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude "data/renders/*"`.
3. Open `http://localhost:8000/`.
4. Log in as a student.
5. Go to `/student/personas`.
6. Open any available teacher card.
7. Ask a topic covered by that teacher's uploaded videos.
8. Expected result: the session starts from the matched teacher roadmap when one is available.

Teacher path:

1. Log in as a teacher.
2. Go to `/teacher/dashboard`.
3. Confirm the persona overview, prompt editor, avatar/voice settings, videos list, and stats render.
4. Go to `/teacher/upload` to add another video; it should update the same teacher persona, not create a separate persona per video.
