from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import (
    ASSEMBLYAI_API_KEY,
    OPENAI_API_KEY,
    STT_MODEL,
    TEACHER_TRANSCRIPTION_PROVIDER,
    TRANSCRIPTION_PROVIDER,
)

logger = logging.getLogger("parallea.transcribe")

_LOCAL_PROVIDER_NAMES = {"local", "whisper", "local-whisper", "openai-whisper"}
_RAILWAY_ENV_VARS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_DEPLOYMENT_ID",
    "RAILWAY_PUBLIC_DOMAIN",
    "RAILWAY_STATIC_URL",
)


def _extract_audio(video_path: str) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", wav_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return wav_path


def _is_production_like_env() -> bool:
    if any((os.getenv(name) or "").strip() for name in _RAILWAY_ENV_VARS):
        return True
    for name in ("PARALLEA_ENV", "ENVIRONMENT", "APP_ENV", "PYTHON_ENV", "NODE_ENV"):
        if (os.getenv(name) or "").strip().lower() in {"prod", "production"}:
            return True
    return False


def _normalize_provider(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value or value == "auto":
        return ""
    if value in {"assemblyai", "assembly-ai", "assembly"}:
        return "assemblyai"
    if value == "openai":
        return "openai"
    if value in _LOCAL_PROVIDER_NAMES:
        return "local"
    raise RuntimeError(
        "Unsupported teacher transcription provider "
        f"'{raw}'. Use assemblyai, openai, local, or whisper."
    )


def _selected_teacher_transcription_provider() -> str:
    teacher_provider = _normalize_provider(TEACHER_TRANSCRIPTION_PROVIDER)
    if teacher_provider:
        return teacher_provider

    shared_provider = _normalize_provider(TRANSCRIPTION_PROVIDER)
    if shared_provider:
        return shared_provider

    if ASSEMBLYAI_API_KEY:
        if _is_production_like_env():
            return "assemblyai"
        return "assemblyai"

    if OPENAI_API_KEY:
        return "openai"

    raise RuntimeError(
        "Teacher upload transcription provider is not configured. "
        "Set TEACHER_TRANSCRIPTION_PROVIDER=assemblyai with ASSEMBLYAI_API_KEY "
        "for Railway, or set TEACHER_TRANSCRIPTION_PROVIDER=local to explicitly "
        "allow local Whisper."
    )


def _effective_teacher_transcription_provider() -> str:
    provider = _selected_teacher_transcription_provider()
    if provider == "assemblyai" and not ASSEMBLYAI_API_KEY:
        if OPENAI_API_KEY:
            logger.warning(
                "teacher transcription provider assemblyai selected but "
                "ASSEMBLYAI_API_KEY is missing; falling back to openai"
            )
            return "openai"
        raise RuntimeError(
            "Teacher transcription provider is assemblyai but ASSEMBLYAI_API_KEY "
            "is missing. Refusing to fall back to local Whisper."
        )
    if provider == "openai" and not OPENAI_API_KEY:
        raise RuntimeError(
            "Teacher transcription provider is openai but OPENAI_API_KEY is missing. "
            "Refusing to fall back to local Whisper."
        )
    return provider


def log_teacher_transcription_status() -> str:
    provider = _effective_teacher_transcription_provider()
    logger.info("teacher transcription provider: %s", provider)
    logger.info(
        "teacher transcription config assemblyai_key_present=%s openai_key_present=%s local_whisper_enabled=%s",
        bool(ASSEMBLYAI_API_KEY),
        bool(OPENAI_API_KEY),
        provider == "local",
    )
    return provider


def _load_assemblyai():
    try:
        import assemblyai as aai
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Teacher transcription provider is assemblyai but the assemblyai "
            "package is not installed."
        ) from exc
    aai.settings.api_key = ASSEMBLYAI_API_KEY
    return aai


def _assemblyai_chunks(wav_path: str, interval: int = 10):
    aai = _load_assemblyai()
    config = aai.TranscriptionConfig(speech_models=[aai.SpeechModel.universal])
    transcript = aai.Transcriber().transcribe(wav_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")
    chunks = {}
    for word in transcript.words or []:
        start_sec = word.start / 1000.0
        bucket = int(start_sec // interval)
        chunks.setdefault(bucket, {"start_sec": bucket * interval, "end_sec": bucket * interval + interval, "text": ""})
        chunks[bucket]["text"] = (chunks[bucket]["text"] + " " + word.text).strip()
    text = (getattr(transcript, "text", "") or "").strip()
    if not chunks and text:
        chunks[0] = {"start_sec": 0, "end_sec": interval, "text": text}
    return chunks


def _response_to_dict(response) -> dict:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    data = {}
    for key in ("text", "segments", "words"):
        if hasattr(response, key):
            data[key] = getattr(response, key)
    return data


def _segment_to_dict(segment) -> dict:
    if isinstance(segment, dict):
        return segment
    if hasattr(segment, "model_dump"):
        return segment.model_dump()
    return {
        "start": getattr(segment, "start", 0),
        "end": getattr(segment, "end", None),
        "text": getattr(segment, "text", ""),
    }


def _openai_chunks(wav_path: str, interval: int = 10):
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Teacher transcription provider is openai but the openai package is not installed."
        ) from exc

    client = OpenAI(api_key=OPENAI_API_KEY)
    model = STT_MODEL or "whisper-1"
    with open(wav_path, "rb") as audio_file:
        try:
            response = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        except TypeError:
            audio_file.seek(0)
            response = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format="verbose_json",
            )

    payload = _response_to_dict(response)
    chunks = {}
    for raw_segment in payload.get("segments") or []:
        segment = _segment_to_dict(raw_segment)
        start_sec = float(segment.get("start") or 0)
        bucket = int(start_sec // interval)
        chunks.setdefault(bucket, {"start_sec": bucket * interval, "end_sec": bucket * interval + interval, "text": ""})
        text = (segment.get("text") or "").strip()
        chunks[bucket]["text"] = (chunks[bucket]["text"] + " " + text).strip()

    if not chunks and (payload.get("text") or "").strip():
        chunks[0] = {"start_sec": 0, "end_sec": interval, "text": payload["text"].strip()}
    return chunks


_whisper_model = None
def _get_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    try:
        import whisper
    except Exception:
        return None
    logger.info("teacher transcription loading local Whisper model=tiny")
    _whisper_model = whisper.load_model("tiny")
    return _whisper_model

def _whisper_chunks(wav_path: str, interval: int = 10):
    model = _get_whisper()
    if model is None:
        raise RuntimeError("No transcription engine available.")
    result = model.transcribe(wav_path, fp16=False)
    chunks = {}
    for seg in result.get("segments", []):
        start_sec = float(seg["start"])
        bucket = int(start_sec // interval)
        chunks.setdefault(bucket, {"start_sec": bucket * interval, "end_sec": bucket * interval + interval, "text": ""})
        text = (seg.get("text") or "").strip()
        chunks[bucket]["text"] = (chunks[bucket]["text"] + " " + text).strip()
    return chunks


def transcribe_with_timestamps(video_path: str, interval: int = 10):
    provider = log_teacher_transcription_status()
    wav_path = _extract_audio(video_path)
    try:
        if provider == "assemblyai":
            chunks = _assemblyai_chunks(wav_path, interval=interval)
        elif provider == "openai":
            chunks = _openai_chunks(wav_path, interval=interval)
        else:
            chunks = _whisper_chunks(wav_path, interval=interval)
        return chunks
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


def save_chunks(chunks, output_path="data/chunks.json"):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    saveable = {str(idx): {"start_sec": c["start_sec"], "end_sec": c["end_sec"], "text": c["text"]} for idx, c in chunks.items()}
    Path(output_path).write_text(json.dumps(saveable, indent=2, ensure_ascii=False), encoding="utf-8")


def load_chunks(path="data/chunks.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(k): v for k, v in data.items()}
