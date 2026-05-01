# Parallea

FastAPI-based AI teaching-persona platform.

Teachers upload lesson videos. The backend transcribes them, updates one evolving teacher persona, creates roadmaps, and keeps the existing visual explanation and Manim pipeline available for immersive tutoring.

## Current UX

- `/` - persona-first homepage
- `/auth/signup` - email/password signup with teacher/student roles
- `/auth/login` - email/password login
- `/teacher/dashboard` - teacher persona overview, prompt editor, avatar/voice settings, stats, and uploaded videos
- `/teacher/upload` - teacher-only video upload and persona/roadmap processing
- `/teacher/roadmaps` - teacher roadmap list
- `/student/personas` - student teacher-persona browse
- `/student/learn/{personaId}` - immersive learning from a selected teacher persona

Old public video-first routes now redirect or are disabled. Backend video, transcription, roadmap, TTS, and Manim logic is preserved.

## Dev Demo Accounts

The dev seed runs on boot unless `PARALLEA_ENABLE_DEV_SEED=0` or `PARALLEA_ENV=production`.

- Teacher: `teacher@example.com` / `password123`
- Student: `student@example.com` / `password123`

The seed includes a `Guitar Coach` persona at `per_demo_guitar_coach`, based on the guitar lesson demo, with one teacher video, one roadmap, and six roadmap parts.

## Environment

Copy `.env.example` into your deployment platform or local shell.

Most important variables:

- `GEMINI_API_KEY` for Gemini segmentation, frame planning, and storyboard planning
- `GROQ_API_KEY` for the first-answer teaching pass
- `OPENAI_API_KEY` for the persona/answer pipeline when enabled
- `PARALLEA_TEACHING_PIPELINE_MODEL` for the active student speech + Manim generation path
- `PARALLEA_VISUAL_MODEL` for Manim repair generation
- `PARALLEA_OPENAI_PIPELINE_MODEL` for the legacy OpenAI Manim pipeline
- `PARALLEA_GROQ_FIRST_ANSWER_MODEL` for the initial answer/lesson decision
- `PARALLEA_GEMINI_TEACHING_MODEL`, `PARALLEA_GEMINI_SEGMENT_MODEL`, `PARALLEA_GEMINI_FRAME_MODEL`, and `PARALLEA_GEMINI_STORYBOARD_MODEL`
- `PARALLEA_DEFAULT_VOICE_ID` for Edge TTS
- `PARALLEA_DATA_DIR`, `PARALLEA_UPLOADS_DIR`, `PARALLEA_THUMBNAILS_DIR`, `PARALLEA_AUDIO_DIR`, and `PARALLEA_SESSIONS_DIR`

Optional:

- `ASSEMBLYAI_API_KEY` for remote upload transcription if you install `requirements.optional.txt`
- `PARALLEA_ENABLE_DEV_SEED=0` to disable demo users/persona

## Install

```bash
pip install -r requirements.txt
```

Optional extras:

```bash
pip install -r requirements.optional.txt
```

`ffmpeg` must be available in `PATH` for thumbnail generation and video audio extraction.

## Run

```bash
python dev_server.py
```

If you call uvicorn directly, keep generated assets out of the reload watcher:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude "data/renders/*" --reload-exclude "manim_runtime/*" --reload-exclude "public/generated/*" --reload-exclude "audio-response/*" --reload-exclude "rendered-scenes/*" --reload-exclude "*.mp4" --reload-exclude "*.mp3" --reload-exclude "*.wav"
```

Health checks:

```bash
GET /health
GET /health/manim
```

Manim LaTeX setup on Windows:

1. Install MiKTeX from https://miktex.org/download.
2. Restart the terminal, VS Code, and backend.
3. Verify both commands work:

```bash
latex --version
dvisvgm --version
```

4. Run:

```bash
python -m backend.scripts.test_manim_render
```

If LaTeX is not installed, set `MANIM_ALLOW_MATHTEX=0` or leave `MANIM_ALLOW_MATHTEX=auto`; the system uses Text-based fallback visuals. See `docs/MANIM_LATEX_SETUP_WINDOWS.md` for full Windows setup notes.

Unified teaching pipeline checks:

```bash
python -m backend.scripts.test_teaching_pipeline_routing
python -m backend.scripts.test_old_manim_pipeline_contract
```

These scripts verify that the active clarification/persona-only route returns timestamped speech, Manim visual segments, a playable public video URL, a cached second render, and the restored `/chat-stream` OpenAI-Manim contract. The runtime app uses `PARALLEA_TEACHING_PIPELINE_PROVIDER` and `PARALLEA_TEACHING_PIPELINE_MODEL` for the combined speech + Manim OpenAI call.

Rendered Manim videos are served from `/rendered-scenes/manim/<hash>.mp4` by default, but the files are written outside the watched source tree. `MANIM_OUTPUT_DIR` and `MANIM_PUBLIC_BASE_URL` can override that path when needed.

## Test The Guitar Persona

1. Start the app.
2. Log in as `student@example.com` / `password123`.
3. Open `/student/personas`.
4. Select `Guitar Coach`.
5. Ask `I want to learn chords`.
6. The topic router should enter `video_context` and load the matching guitar roadmap parts.
7. Ask `jazz harmony`.
8. The avatar should ask for confirmation before using `persona_only` mode.

## Deployment Notes

- Use `PARALLEA_ENV=production` or `PARALLEA_ENABLE_DEV_SEED=0` in production.
- The app is designed for same-origin hosting as a single FastAPI service.
- Server-side TTS uses Edge TTS only. No local TTS model files are required.
- Mount runtime storage in production if uploads, thumbnails, transcripts, audio, and generated renders must persist.
- Browser microphone features require HTTPS or localhost.
- Generated Manim scene/work/debug/public files are written to an external runtime directory by default so local `--reload` does not restart the API during renders.
- If Manim behaves unpredictably on a very new Python version, prefer Python 3.11 or 3.12 and point `PARALLEA_MANIM_PYTHON` at that interpreter.

## Docker

```bash
docker build -t parallea .
docker run --rm -p 8000:8000 --env-file .env -v ${PWD}/data:/app/data -v ${PWD}/uploads:/app/uploads -v ${PWD}/thumbnails:/app/thumbnails parallea
```

See `docs/ux-replacement-audit.md` for the route replacement audit and test notes.
