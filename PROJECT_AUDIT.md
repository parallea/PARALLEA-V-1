# Project Audit

Generated from local code inspection on 2026-03-27.

This audit predates the latest hosting simplification work. For the current deployment shape and active runtime paths, use `SIMPLIFICATION_REPORT.md`.

## Project overview

- App type: mixed FastAPI backend plus static HTML/CSS/vanilla JS frontend.
- Framework detected: FastAPI on Python. This is not a Next.js, React build, or Vite app.
- Primary purpose: turn uploaded lesson videos into a voice-driven tutoring experience with avatar selection, TTS playback, source-clip replay, and synchronized board visuals.

## Current stack

### Backend

- Python
- FastAPI
- Uvicorn
- `python-multipart` for uploads/forms
- permissive CORS (`allow_origins=["*"]`)

### Frontend

- Static HTML files served directly by FastAPI
- Vanilla JavaScript
- No bundler and no JS build step
- Google Fonts via CDN

### AI / LLM / retrieval

- Groq API, default model `llama-3.3-70b-versatile`
- Gemini API, optional visual-planner path
- Heuristic local fallback responses when remote AI is unavailable
- DDGS web search fallback when video context is weak and package is installed
- Optional reranker via `sentence-transformers`
- Optional Chroma/vectorstore hooks are referenced, but `vectorstore.py` is not present in this repo

### Voice / transcription / media

- Kokoro ONNX local TTS
- `onnxruntime`
- `edge-tts` fallback
- OpenAI Whisper local transcription
- AssemblyAI optional transcription
- `ffmpeg` for thumbnailing, audio extraction, and some audio conversion

### Visualization / avatars

- Three.js avatar rendering in `avatar-select.html` and `learn.html`
- Custom canvas-based whiteboard renderer in `learn.html`
- Semantic scene payload generator in `blackboard_visuals.py`
- Mermaid and Chart.js are loaded in `learn.html`
- SVG board assets in `board_assets/`

### Build / deployment

- `requirements.txt` and `requirements.optional.txt`
- `Dockerfile`
- Real package manager: `pip`
- `package-lock.json` exists but there is no `package.json`; it is effectively unused

## Folder structure summary

### Top-level folders

- `board_assets/`: SVG assets used by the board/visual system.
- `data/`: runtime data, including `videos.json`, cached audio, and per-video transcript folders.
- `thumbnails/`: generated video thumbnails.
- `uploads/`: uploaded lesson videos.
- `.venv/`: local Python virtualenv, not part of app runtime.
- `.idea/`: IDE metadata.
- `__pycache__/`: Python bytecode cache.

### Top-level files

- `main.py`: main FastAPI app, routes, session handling, upload pipeline, video tutoring logic.
- `config.py`: environment loading, path setup, avatar presets, runtime defaults.
- `voice.py`: Kokoro/Edge TTS and Whisper question transcription.
- `transcribe.py`: upload transcription pipeline and chunk persistence.
- `rag.py`: remote teacher/visual planning, heuristic fallbacks, optional retrieval/web search.
- `blackboard_visuals.py`: semantic-scene visual payload builder.
- `board_scene_library.py`, `board_elements.py`, `board_asset_library.py`: visual scene vocab and validation.
- `index.html`: landing page, library, upload modal.
- `player.html`: video playback page before immersion mode.
- `avatar-select.html`: avatar picker and voice preview page.
- `learn.html`: main uploaded-video immersive learning UI.
- `Dockerfile`: container packaging.
- `requirements.txt`, `requirements.optional.txt`: Python dependencies.
- `.env.example`: example env file, but it is stale relative to current code.
- `.env`: local secrets/runtime overrides exist in workspace; do not deploy this file.

## Main entry points

- Server entry point: `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`
- Main app object: `main.py`
- Main landing route: `/`
- Main uploaded-video learning route: `/learn`

## Runtime and architecture

### End-to-end flow for uploaded lessons

1. `index.html` loads `/videos` and shows the video library.
2. Upload uses `POST /upload`.
3. Backend saves the uploaded file into `uploads/`, generates a thumbnail with `ffmpeg`, extracts/transcribes audio, saves transcript chunks to `data/video_<id>/chunks.json`, and writes metadata to `data/videos.json`.
4. `player.html` loads `/video-meta/{video_id}` and streams `/video/{video_id}`.
5. `avatar-select.html` loads `/avatar-presets` and `/video-meta/{video_id}`, previews the selected avatar voice via `/avatar-sample`, then redirects into `/learn`.
6. `learn.html` calls `/set-avatar`, then `/greet`, then loops through microphone capture -> `/transcribe-question` -> `/chat`.
7. `/chat` returns answer text, suggestions, board actions, visual payload, and optionally a "reference bridge" clip range.
8. `learn.html` replays the relevant source clip from `/video/{video_id}`, then plays TTS audio from `/audio-response/{name}` and drives synchronized visuals on the board.

### State management

- Backend state: in-memory `_sessions` dict in `main.py`.
- Frontend state: page-local JS variables in each HTML file.
- Session IDs are generated client-side and passed in query params / form payloads.
- There is no database and no persistent session store.

### How frontend talks to backend

- All pages use `location.origin` as the API base.
- That means the app currently expects frontend and backend to be hosted on the same origin.
- Separate frontend/backend hosting is not plug-and-play without adding a configurable API base URL or a reverse proxy.

## Routes and endpoints

### Pages

- `GET /`
- `GET /player`
- `GET /avatar-select`
- `GET /learn`

### API / asset routes

- `GET /health`
- `GET /videos`
- `GET /video-meta/{video_id}`
- `GET /thumbnail/{video_id}`
- `GET /video/{video_id}`
- `DELETE /video/{video_id}`
- `GET /audio-response/{name}`
- `GET /avatar-presets`
- `POST /warm-avatar-audio`
- `POST /avatar-sample`
- `POST /set-avatar`
- `GET /session-state/{sid}`
- `POST /transcribe-question`
- `POST /upload`
- `POST /greet`
- `POST /chat`
- `POST /signal`

### Notes on partial / unused routes

- `POST /signal` exists but the current HTML clients do not call it.
- `POST /warm-avatar-audio` exists but is not part of the normal UI flow.
- `GET /session-state/{sid}` exists, but sessions are still in memory only.

## Features detected

- Upload and index lesson videos
- Generate thumbnails from uploaded videos
- Transcribe uploaded videos into timestamped chunk JSON
- Stream videos with HTTP Range support
- Avatar selection with cached preview audio
- Voice-driven Q&A over uploaded lessons
- TTS audio caching per avatar/session/lesson
- Clip-aware tutoring that can replay relevant source moments before explanation
- Synchronized board visuals driven by semantic scene payloads
- Heuristic local teaching fallback when remote model calls fail
## APIs, services, and models used

| Service / model | Where used | Purpose | Status |
| --- | --- | --- | --- |
| Kokoro ONNX (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | `voice.py`, `config.py` | local TTS | active locally |
| Edge TTS | `voice.py` | fallback TTS when Kokoro fails | active if enabled |
| Whisper tiny | `voice.py`, `transcribe.py` | mic-question transcription and upload transcription fallback | active |
| AssemblyAI | `transcribe.py` | upload transcription | optional |
| Groq | `rag.py` | remote teacher answers and optional visual planning | optional |
| Gemini | `rag.py` | optional remote visual planning | optional |
| DDGS | `rag.py` | web search fallback when local context is weak | optional |
| CrossEncoder `cross-encoder/ms-marco-MiniLM-L-6-v2` | `rag.py` | optional reranking | optional / heavy |
| ChromaDB / vectorstore hooks | `main.py`, `rag.py` | optional retrieval | referenced, but local `vectorstore.py` is missing |

## Critical files

- `main.py`: most important backend file.
- `config.py`: central runtime/env bootstrap.
- `voice.py`: speech output and mic transcription.
- `transcribe.py`: upload processing path.
- `rag.py`: AI behavior, fallback logic, optional remote services.
- `learn.html`: primary uploaded-video product UI.
- `Dockerfile`: current deployment packaging.
- `.dockerignore`: currently affects deploy correctness in important ways.
- `data/videos.json`: source of truth for indexed videos, but currently contains environment-specific absolute paths.

## Dependency audit

### `requirements.txt`

- `fastapi`: API server.
- `uvicorn[standard]`: ASGI server.
- `python-multipart`: file uploads/forms.
- `groq`: remote LLM calls.
- `edge-tts`: fallback TTS.
- `kokoro-onnx`: local TTS model runtime.
- `onnxruntime`: ONNX execution.
- `assemblyai`: optional upload transcription.
- `openai-whisper`: local transcription.
- `torch`: Whisper runtime dependency and major hosting weight.

### `requirements.optional.txt`

- `google-genai`: Gemini visual planner path.
- `sentence-transformers`: reranker.
- `chromadb`: retrieval store.
- `ddgs`: web search fallback.

### Deployment-impacting dependencies

- `torch` + Whisper can increase memory use and build time.
- `ffmpeg` is required at runtime.
- Kokoro local models are large: `kokoro-v1.0.onnx` is about 325 MB and `voices-v1.0.bin` is about 28 MB.
- Three.js, Mermaid, Chart.js, RoughJS, and Google Fonts are loaded from external CDNs, so frontend rendering depends on outbound internet access from the browser.

## Environment variables

### Active code paths

| Variable | Used in | Service / concern | Required? | Notes |
| --- | --- | --- | --- | --- |
| `GROQ_API_KEY` | `config.py`, `rag.py`, `main.py` | Groq | Optional | Needed for remote teacher flow. |
| `GEMINI_API_KEY` | `config.py`, `rag.py` | Gemini | Optional | Needed only if remote visuals use Gemini. |
| `ASSEMBLYAI_API_KEY` | `config.py`, `transcribe.py` | AssemblyAI | Optional | If absent, uploads fall back to local Whisper. |
| `PARALLEA_ENABLE_REMOTE_TEACHER` | `rag.py` | feature flag | Optional | Defaults on if Groq key exists. |
| `PARALLEA_ENABLE_REMOTE_VISUALS` | `rag.py` | feature flag | Optional | Off by default. |
| `PARALLEA_ENABLE_RERANKER` | `rag.py` | feature flag | Optional | Heavy path. |
| `PARALLEA_REMOTE_VISUAL_PROVIDER` | `rag.py` | groq/gemini switch | Optional | Defaults to `groq`. |
| `PARALLEA_TEACHER_MODEL` | `rag.py` | model selector | Optional | Current local env uses Groq model naming. |
| `PARALLEA_VISUAL_MODEL` | `rag.py` | model selector | Optional | Used only when remote visuals enabled. |
| `PARALLEA_DATA_DIR` | `config.py` | filesystem | Optional | Base runtime data dir. |
| `PARALLEA_UPLOADS_DIR` | `config.py` | filesystem | Optional | Uploaded video storage. |
| `PARALLEA_THUMBNAILS_DIR` | `config.py` | filesystem | Optional | Thumbnail storage. |
| `PARALLEA_AUDIO_DIR` | `config.py` | filesystem | Optional | Generated/cached audio storage. |
| `PARALLEA_SESSIONS_DIR` | `config.py` | filesystem | Optional | Directory is created, but sessions are still stored in memory only. |
| `PARALLEA_VIDEOS_DB` | `config.py` | filesystem | Optional | Metadata JSON path. |
| `KOKORO_MODEL_PATH` | `config.py`, `main.py`, `voice.py` | local TTS | Conditionally required | Needed if not relying on Edge TTS fallback. |
| `KOKORO_VOICES_PATH` | `config.py`, `main.py`, `voice.py` | local TTS | Conditionally required | Same as above. |
| `KOKORO_DEFAULT_VOICE` | `config.py`, `voice.py` | local TTS | Optional | Default Kokoro voice. |
| `KOKORO_DEFAULT_SPEED` | `config.py`, `voice.py` | local TTS | Optional | TTS speed. |
| `PARALLEA_ENABLE_EDGE_TTS_FALLBACK` | `config.py`, `voice.py` | TTS fallback | Optional but practical | Important if Kokoro files are not present in deploy. |
| `PARALLEA_TTS_AUDIO_EXTENSION` | `config.py`, `voice.py` | audio format | Optional | Current local env uses `.wav`. |
| `PARALLEA_VOICE_AVA_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |
| `PARALLEA_VOICE_MIA_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |
| `PARALLEA_VOICE_ZARA_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |
| `PARALLEA_VOICE_LINA_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |
| `PARALLEA_VOICE_NOAH_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |
| `PARALLEA_VOICE_ARIN_ID` | `config.py` | avatar voice mapping | Optional | Uses Kokoro default if unset. |

### Example / config drift

- `.env.example` still contains `ELEVENLABS_*` variables, but the current codebase does not reference them.
- `.env.example` does not document several active variables now used by code, including:
  - `PARALLEA_ENABLE_REMOTE_TEACHER`
  - `PARALLEA_ENABLE_REMOTE_VISUALS`
  - `PARALLEA_ENABLE_RERANKER`
  - `PARALLEA_REMOTE_VISUAL_PROVIDER`
  - `PARALLEA_TEACHER_MODEL`
  - `PARALLEA_VISUAL_MODEL`
  - `PARALLEA_VIDEOS_DB`
  - `KOKORO_DEFAULT_SPEED`
  - `PARALLEA_TTS_AUDIO_EXTENSION`

### Secret handling

- The workspace `.env` currently contains populated third-party API keys.
- Those values are not exposed in frontend code, but they should still be treated as secrets and managed through host environment settings.
- If this workspace or file has been shared, rotate the keys.

## Hosting readiness audit

### Can frontend and backend be hosted together?

- Yes, and that is the simplest deployment shape.
- In fact, current code assumes same-origin hosting because frontend pages use `location.origin` for API requests.

### Can frontend and backend be hosted separately?

- Not cleanly right now.
- You would need either:
  - a reverse proxy so both appear under one origin, or
  - a code change to make API base URL configurable.

### Best fit hosting options

| Platform | Fit | Notes |
| --- | --- | --- |
| Railway | Good | Single Docker service plus attached volume is a practical fast path. |
| Render | Good | Similar to Railway; persistent disk is important. |
| Docker on VPS | Best control | Safest if you want full access to ffmpeg, model files, and disk. |
| Vercel | Poor fit | Serverless/file-write/model-size/media-processing mismatch. |
| Netlify | Poor fit | Static hosting is fine for HTML, but backend/media/model/runtime needs make it a poor fit as the primary host. |

### Fastest realistic hosting recommendation

- Host the whole app as one Dockerized FastAPI service on Railway, Render, or a VPS.
- Add a persistent volume and point `PARALLEA_DATA_DIR`, `PARALLEA_UPLOADS_DIR`, `PARALLEA_THUMBNAILS_DIR`, and `PARALLEA_AUDIO_DIR` to that volume.
- Keep `PARALLEA_ENABLE_EDGE_TTS_FALLBACK=true` for the first deploy unless you mount Kokoro model files.
- Use managed secrets on the host instead of shipping `.env`.
- Prefer HTTPS-enabled hosting because microphone features depend on secure browser contexts.

## Deployment blockers

### High-risk deployment blockers

1. Public app has no auth.
   - Anyone who can reach the app can currently upload videos and delete videos.
   - `DELETE /video/{video_id}` is open, and upload/chat endpoints are unauthenticated.

2. Runtime depends on local disk writes.
   - Videos, thumbnails, transcript chunks, and generated audio are all written to local filesystem paths.
   - Ephemeral hosts will lose data across restarts or deploys unless a persistent volume is mounted.

3. `data/videos.json` stores absolute machine-specific paths.
   - Current metadata includes values like `D:\0\data\video_...\chunks.json`.
   - Moving seeded data from this machine to a container or Linux host will break those references.

4. Docker packaging does not match the local runtime.
   - `.dockerignore` excludes the Kokoro model files, so the container loses the local TTS path unless you mount/provide them separately.
   - `.dockerignore` also excludes `uploads/` and `thumbnails/`, but current `data/videos.json` is still present, so a container can boot with metadata pointing at media files that are not inside the image.

5. Upload processing is synchronous and heavy.
   - `POST /upload` does file save, thumbnail extraction, transcription, and optional vectorstore build before returning.
   - On smaller hosts this can block workers, run slowly, or hit request timeout limits.

### Medium-risk blockers / sharp edges

- Browser microphone features require HTTPS or localhost.
  - `learn.html` depends on `getUserMedia`, `MediaRecorder`, and `AudioContext`.

- Optional retrieval path is incomplete.
  - `main.py` and `rag.py` reference `vectorstore`, but `vectorstore.py` is not in this repo.
  - Retrieval falls back to chunk heuristics, so the app still runs, but the intended vectorstore path is incomplete.

- Dockerfile installs only `requirements.txt`.
  - If you later enable Gemini visuals, reranking, or DDGS-based web fallback, the image will need optional dependencies too.

- Current runtime data is already inconsistent.
  - `data/videos.json` indexes one video, while the repo contains more uploaded files/transcript folders than that.
  - This suggests orphaned runtime artifacts and weak cleanup guarantees.

- External browser CDNs are required.
  - Google Fonts, Three.js, Mermaid, Chart.js, and RoughJS are all loaded remotely in the browser.

## What is required to host it as soon as possible

### Minimum requirements

- Python-capable container host
- `ffmpeg` installed
- HTTPS
- managed env vars
- persistent storage for runtime data
- decision on TTS mode:
  - mount Kokoro model files, or
  - rely on Edge TTS fallback

### Recommended first-production configuration

- `PARALLEA_ENABLE_EDGE_TTS_FALLBACK=true`
- `GROQ_API_KEY` set if you want the remote teacher behavior
- `ASSEMBLYAI_API_KEY` set if you want to offload upload transcription from local Whisper
- `PARALLEA_ENABLE_REMOTE_VISUALS=0`
- `PARALLEA_ENABLE_RERANKER=0`
- persistent volume mounted for data/uploads/thumbnails/audio

## Fastest path to production

1. Deploy the current FastAPI app as a single Docker service on Railway/Render/VPS.
2. Remove or reset local seeded runtime metadata before deploy, or ignore all runtime `data/` content and start from a clean mounted volume.
3. Move all secrets out of `.env` and into host secret settings; rotate existing keys if needed.
4. Keep Edge TTS fallback enabled for version 1 unless you deliberately mount Kokoro files into the container.
5. Attach a persistent volume and point the runtime directories at it.
6. Put the app behind HTTPS.
7. Add at least basic auth or private-network protection before exposing upload/delete routes publicly.

## Suggested next engineering steps

1. Make `videos.json` store relative paths instead of absolute paths.
2. Add auth or private access controls before public deployment.
3. Normalize Docker/runtime behavior:
   - either mount Kokoro files explicitly, or
   - remove the local-TTS assumption from the first deployment target.
4. Clean up runtime data handling:
   - start from an empty mounted data volume
   - avoid shipping local dev metadata in the image
5. Update `.env.example` to match real code and remove stale `ELEVENLABS_*` entries.
6. If upload scale matters, move upload transcription off the request path into a background job.

## Practical summary

- The project is already locally runnable and locally rich in features.
- It is closest to production when treated as a single Dockerized FastAPI app with persistent storage, HTTPS, and managed secrets.
- It is not ready for a clean public deploy on serverless/static-first hosts without addressing auth, runtime storage, and packaging mismatches first.
