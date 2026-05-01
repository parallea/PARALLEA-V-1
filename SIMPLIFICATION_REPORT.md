# Parallea Simplification Report

## Files Removed

- `uploads/video_7a6506d2.mp4`
- `thumbnails/7a6506d2.jpg`
- `data/video_7a6506d2/`

## Files Modified

- `config.py`
- `voice.py`
- `transcribe.py`
- `main.py`
- `requirements.txt`
- `requirements.optional.txt`
- `.env.example`
- `README.md`
- `.gitignore`
- `.dockerignore`
- `PROJECT_AUDIT.md`

## Dependencies Removed

- `assemblyai` from `requirements.txt`

## Dependencies Kept

- `fastapi`
- `uvicorn[standard]`
- `python-multipart`
- `groq`
- `edge-tts`
- `openai-whisper`
- `torch`

## Old TTS Flow vs New TTS Flow

### Old

- Multiple TTS paths were expected historically, including Kokoro ONNX and an Edge fallback.
- TTS configuration depended on more environment-specific assumptions and optional format conversion.

### New

- Edge TTS is the only server-side TTS provider.
- Audio is always generated as cached `.mp3`.
- Avatar-specific voice mapping still works through lightweight env-based overrides.
- If a custom avatar voice is invalid, synthesis retries with the avatar fallback voice and then the global default voice.

## Env Vars Removed

- `PARALLEA_TTS_AUDIO_EXTENSION`
- Historical Kokoro env vars are no longer part of the active deploy shape.

## Env Vars Kept

- `GROQ_API_KEY`
- `PARALLEA_ENABLE_REMOTE_TEACHER`
- `PARALLEA_TEACHER_MODEL`
- `ASSEMBLYAI_API_KEY`
- `PARALLEA_DEFAULT_VOICE_ID`
- `PARALLEA_TTS_RATE`
- `PARALLEA_VOICE_AVA_ID`
- `PARALLEA_VOICE_MIA_ID`
- `PARALLEA_VOICE_ZARA_ID`
- `PARALLEA_VOICE_LINA_ID`
- `PARALLEA_VOICE_NOAH_ID`
- `PARALLEA_VOICE_ARIN_ID`
- `PARALLEA_DATA_DIR`
- `PARALLEA_UPLOADS_DIR`
- `PARALLEA_THUMBNAILS_DIR`
- `PARALLEA_AUDIO_DIR`
- `PARALLEA_SESSIONS_DIR`
- `PARALLEA_VIDEOS_DB`

## Default Hosting Configuration

- Single FastAPI service
- Same-origin HTML pages served by FastAPI
- Edge TTS as the only TTS provider
- Groq remote teacher enabled only when configured
- Remote visuals off by default
- Reranker disabled in practice and reduced to lightweight local scoring
- No local TTS model files required
- Runtime media and metadata expected on mounted storage

## Remaining Deployment Blockers

- `ffmpeg` still needs to be available for thumbnail generation and upload transcription audio extraction.
- Whisper and Torch are still the heaviest local runtime pieces because local speech transcription remains part of the core product flow.
- Remote teacher answers still depend on a valid `GROQ_API_KEY`.

## Exact Commands To Run Locally

```bash
pip install -r requirements.txt
pip install -r requirements.optional.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
docker build -t parallea .
docker run --rm -p 8000:8000 --env-file .env -v ${PWD}/data:/app/data -v ${PWD}/uploads:/app/uploads -v ${PWD}/thumbnails:/app/thumbnails parallea
```
