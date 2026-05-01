from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

try:
    import edge_tts
except Exception:
    edge_tts = None

try:
    import whisper
except Exception:
    whisper = None

from config import (
    AUDIO_DIR,
    OPENAI_API_KEY,
    PARALLEA_DEFAULT_VOICE_ID,
    PARALLEA_TTS_RATE,
    STT_LANGUAGE,
    STT_MODEL,
    STT_PROVIDER,
    TTS_AUDIO_EXTENSION,
)


logger = logging.getLogger("parallea.voice")
_whisper_model = None
_whisper_model_name = None
MIN_STT_AUDIO_BYTES = 2048
FFMPEG_LOG_LIMIT = 12000


class VoicePipelineError(RuntimeError):
    def __init__(self, code: str, message: str, *, debug: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.debug = debug or {}


class AudioInputError(VoicePipelineError):
    pass


class AudioConversionError(VoicePipelineError):
    pass


def _voice_candidates(voice_id: str, fallback_voice: str | None = None) -> list[str]:
    candidates: list[str] = []
    for value in [voice_id, fallback_voice, PARALLEA_DEFAULT_VOICE_ID]:
        name = (value or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    return candidates


def tts_provider_status() -> dict:
    return {
        "provider": "edge-tts",
        "available": edge_tts is not None,
        "default_voice": PARALLEA_DEFAULT_VOICE_ID,
        "audio_extension": TTS_AUDIO_EXTENSION,
    }


def stt_provider_status() -> dict:
    provider = _effective_stt_provider()
    return {
        "provider": provider,
        "model": _effective_stt_model(provider),
        "language": STT_LANGUAGE,
        "openai_configured": bool(OPENAI_API_KEY),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
        "ffprobe_available": bool(shutil.which("ffprobe")),
    }


def _effective_stt_provider() -> str:
    provider = (STT_PROVIDER or "whisper").strip().lower()
    if provider in {"openai", "whisper"}:
        return provider
    return "whisper"


def _effective_stt_model(provider: str | None = None) -> str:
    provider = provider or _effective_stt_provider()
    if STT_MODEL:
        return STT_MODEL
    return "whisper-1" if provider == "openai" else "base"


def _language_hint() -> tuple[str | None, str]:
    raw = (STT_LANGUAGE or "en").strip().lower()
    if raw in {"", "auto", "detect"}:
        return None, "The speaker may use Indian English, Hinglish, Hindi words, or English technical terms."
    if raw in {"hinglish", "en-in", "en_in", "indian-english", "indian_english"}:
        return "en", "The speaker may use Indian English and Hinglish. Preserve English topic words clearly."
    if raw in {"hi", "hindi"}:
        return "hi", "The speaker may use Hindi, Hinglish, or English topic words."
    return raw.split("-")[0], "The speaker may use Indian English or classroom vocabulary."


def _get_whisper(model_name: str | None = None):
    global _whisper_model, _whisper_model_name
    model_name = model_name or _effective_stt_model("whisper")
    if _whisper_model is not None and _whisper_model_name == model_name:
        return _whisper_model
    if whisper is None:
        return None
    try:
        print(f"Loading Whisper {model_name} (STT)...")
        _whisper_model = whisper.load_model(model_name)
        _whisper_model_name = model_name
        print(f"Whisper {model_name} ready")
        return _whisper_model
    except Exception as exc:
        print(f"Whisper init failed: {exc}")
        return None


def _short_log(text: Any, limit: int = FFMPEG_LOG_LIMIT) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def _audio_file_debug(path: str | Path, *, mime_type: str = "") -> dict[str, Any]:
    p = Path(path)
    exists = p.exists()
    size = p.stat().st_size if exists else 0
    header = ""
    if exists and size:
        try:
            header = p.read_bytes()[:24].hex(" ")
        except Exception as exc:  # noqa: BLE001
            header = f"<read failed: {exc}>"
    return {
        "path": str(p),
        "exists": exists,
        "size_bytes": size,
        "mime_type": mime_type,
        "header_hex": header,
    }


def probe_audio_file(path: str, *, mime_type: str = "") -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    base = _audio_file_debug(path, mime_type=mime_type)
    if not ffprobe:
        return {**base, "ffprobe_available": False}
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=12)
    except Exception as exc:  # noqa: BLE001
        return {**base, "ffprobe_available": True, "ffprobe_error": str(exc)}
    parsed: dict[str, Any] = {}
    if proc.stdout:
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = {}
    streams = [item for item in (parsed.get("streams") or []) if isinstance(item, dict)]
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = parsed.get("format") if isinstance(parsed.get("format"), dict) else {}
    duration = None
    for raw in [(audio_stream or {}).get("duration"), fmt.get("duration")]:
        try:
            if raw not in {None, "", "N/A"}:
                duration = float(raw)
                break
        except Exception:
            continue
    info = {
        **base,
        "ffprobe_available": True,
        "ffprobe_returncode": proc.returncode,
        "ffprobe_stdout": _short_log(proc.stdout, 3000),
        "ffprobe_stderr": _short_log(proc.stderr, 3000),
        "duration_sec": duration,
        "format_name": fmt.get("format_name"),
        "codec": (audio_stream or {}).get("codec_name"),
        "sample_rate": (audio_stream or {}).get("sample_rate"),
        "channels": (audio_stream or {}).get("channels"),
        "has_audio_stream": bool(audio_stream),
    }
    logger.info(
        "audio probe path=%s exists=%s bytes=%s mime=%s duration=%s codec=%s sample_rate=%s channels=%s returncode=%s",
        path,
        info["exists"],
        info["size_bytes"],
        mime_type,
        info.get("duration_sec"),
        info.get("codec"),
        info.get("sample_rate"),
        info.get("channels"),
        info.get("ffprobe_returncode"),
    )
    return info


def _validate_audio_input(path: str, *, mime_type: str = "") -> dict[str, Any]:
    debug = probe_audio_file(path, mime_type=mime_type)
    if not debug.get("exists"):
        raise AudioInputError("audio_missing", "No microphone audio file was received.", debug=debug)
    size = int(debug.get("size_bytes") or 0)
    if size <= 0:
        raise AudioInputError("empty_audio", "The microphone audio was empty. Please try again.", debug=debug)
    if size < MIN_STT_AUDIO_BYTES:
        raise AudioInputError("audio_too_small", "The microphone audio was too short or incomplete. Please try again.", debug=debug)
    if debug.get("ffprobe_available") and debug.get("ffprobe_returncode") == 0:
        if not debug.get("has_audio_stream"):
            raise AudioInputError("audio_has_no_stream", "The uploaded microphone file did not contain an audio stream.", debug=debug)
        duration = debug.get("duration_sec")
        if duration is not None and duration <= 0.05:
            raise AudioInputError("audio_duration_zero", "The uploaded microphone audio had no usable duration.", debug=debug)
    return debug


def _run_ffmpeg_convert(src: str, dst: str, *, original_mime_type: str = "") -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH; cannot normalize microphone audio for STT.")
    input_debug = _validate_audio_input(src, mime_type=original_mime_type)
    commands = [
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-i",
            src,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            dst,
        ],
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-fflags",
            "+genpts",
            "-i",
            src,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            dst,
        ],
    ]
    attempts: list[dict[str, Any]] = []
    for attempt, cmd in enumerate(commands, start=1):
        Path(dst).unlink(missing_ok=True)
        try:
            proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=30)
            returncode: int | str = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = "timeout"
            stdout = exc.stdout or ""
            stderr = exc.stderr or f"FFmpeg conversion timed out after {exc.timeout} seconds."
        output_size = Path(dst).stat().st_size if Path(dst).exists() else 0
        attempt_debug = {
            "attempt": attempt,
            "ffmpeg_path": ffmpeg,
            "input_file": src,
            "input_exists": input_debug.get("exists"),
            "input_size_bytes": input_debug.get("size_bytes"),
            "input_mime_type": original_mime_type,
            "output_file": dst,
            "output_size_bytes": output_size,
            "returncode": returncode,
            "stdout": _short_log(stdout),
            "stderr": _short_log(stderr),
            "command": " ".join(cmd),
        }
        attempts.append(attempt_debug)
        if returncode == 0 and output_size > 44:
            logger.info(
                "ffmpeg audio conversion success attempt=%s ffmpeg=%s input=%s bytes=%s mime=%s output=%s output_bytes=%s",
                attempt,
                ffmpeg,
                src,
                input_debug.get("size_bytes"),
                original_mime_type,
                dst,
                output_size,
            )
            return
        logger.error(
            "ffmpeg audio conversion failed attempt=%s ffmpeg=%s input=%s input_exists=%s input_bytes=%s mime=%s output=%s output_bytes=%s returncode=%s stdout=%s stderr=%s",
            attempt,
            ffmpeg,
            src,
            input_debug.get("exists"),
            input_debug.get("size_bytes"),
            original_mime_type,
            dst,
            output_size,
            returncode,
            _short_log(stdout),
            _short_log(stderr),
        )
    raise AudioConversionError(
        "audio_conversion_failed",
        "Could not convert microphone audio. Please try again.",
        debug={
            "input": input_debug,
            "attempts": attempts,
        },
    )


def audio_duration_seconds(path: str) -> float | None:
    info = probe_audio_file(path)
    if not info.get("ffprobe_available"):
        return None
    duration = info.get("duration_sec")
    return duration if isinstance(duration, (int, float)) and duration >= 0 else None


def _prepare_wav_for_stt(audio_path: str, *, original_mime_type: str = "") -> tuple[str, bool, float | None]:
    fd, wav_name = tempfile.mkstemp(prefix="stt_", suffix=".wav")
    os.close(fd)
    try:
        _run_ffmpeg_convert(audio_path, wav_name, original_mime_type=original_mime_type)
        return wav_name, True, audio_duration_seconds(wav_name)
    except Exception:
        Path(wav_name).unlink(missing_ok=True)
        raise


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _whisper_confidence(result: dict[str, Any]) -> dict[str, Any]:
    segments = [seg for seg in (result.get("segments") or []) if isinstance(seg, dict)]
    avg_logprob = _avg([float(seg.get("avg_logprob")) for seg in segments if isinstance(seg.get("avg_logprob"), (int, float))])
    no_speech_prob = _avg([float(seg.get("no_speech_prob")) for seg in segments if isinstance(seg.get("no_speech_prob"), (int, float))])
    confidence = None
    if avg_logprob is not None:
        # Whisper exposes log probabilities rather than a direct confidence.
        # This maps common usable ranges into a rough 0..1 debug score.
        confidence = max(0.0, min(1.0, (avg_logprob + 1.5) / 1.5))
        if no_speech_prob is not None:
            confidence = min(confidence, 1.0 - max(0.0, min(1.0, no_speech_prob)))
    return {
        "confidence": confidence,
        "avg_logprob": avg_logprob,
        "no_speech_prob": no_speech_prob,
        "segments": len(segments),
    }


def _unclear_reason(text: str, *, confidence: float | None, no_speech_prob: float | None, duration_sec: float | None, size_bytes: int) -> str | None:
    cleaned = (text or "").strip()
    if size_bytes <= 1024:
        return "empty_audio"
    if duration_sec is not None and duration_sec < 0.45:
        return "too_short_audio"
    if not cleaned:
        return "empty_transcript"
    if len(cleaned) <= 2:
        return "very_short_transcript"
    lowered = cleaned.lower()
    if lowered in {"uh", "um", "hmm", "mmm", "ah", "oh"}:
        return "filler_only"
    if no_speech_prob is not None and no_speech_prob >= 0.65:
        return "high_no_speech_probability"
    if confidence is not None and confidence < 0.28:
        return "low_confidence"
    return None


def _transcribe_with_local_whisper(wav_path: str) -> dict[str, Any]:
    provider = "whisper"
    model_name = _effective_stt_model(provider)
    model = _get_whisper(model_name)
    if model is None:
        raise RuntimeError("Whisper is unavailable. Install openai-whisper.")
    language, prompt = _language_hint()
    kwargs: dict[str, Any] = {"fp16": False, "initial_prompt": prompt}
    if language:
        kwargs["language"] = language
    result = model.transcribe(wav_path, **kwargs)
    text = (result.get("text") or "").strip()
    confidence_meta = _whisper_confidence(result)
    return {
        "text": text,
        "provider": provider,
        "model": model_name,
        "language": result.get("language") or language or STT_LANGUAGE,
        **confidence_meta,
        "raw": {
            "duration": result.get("duration"),
        },
    }


def _transcribe_with_openai(audio_path: str) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured for STT_PROVIDER=openai.")
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(f"OpenAI SDK is unavailable: {exc}") from exc
    provider = "openai"
    model_name = _effective_stt_model(provider)
    language, prompt = _language_hint()
    client = OpenAI(api_key=OPENAI_API_KEY)
    kwargs: dict[str, Any] = {"model": model_name, "file": None}
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt
    with open(audio_path, "rb") as handle:
        kwargs["file"] = handle
        response = client.audio.transcriptions.create(**kwargs)
    text = (getattr(response, "text", None) or "").strip()
    return {
        "text": text,
        "provider": provider,
        "model": model_name,
        "language": language or STT_LANGUAGE,
        "confidence": None,
        "avg_logprob": None,
        "no_speech_prob": None,
        "segments": None,
        "raw": {},
    }


async def synthesize_to_file(
    text: str,
    voice_id: str,
    lang: str,
    out_path: Path,
    fallback_voice: str | None = None,
) -> Path:
    del lang
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = (text or "").strip()
    if not cleaned:
        raise RuntimeError("No text was provided for synthesis.")
    if edge_tts is None:
        raise RuntimeError("Edge TTS is unavailable. Install edge-tts to enable server-side audio.")
    errors = []
    for voice_name in _voice_candidates(voice_id, fallback_voice):
        fd, temp_name = tempfile.mkstemp(prefix=f"{out_path.stem}_", suffix=TTS_AUDIO_EXTENSION, dir=str(out_path.parent))
        os.close(fd)
        tmp_audio = Path(temp_name)
        try:
            communicator = edge_tts.Communicate(text=cleaned, voice=voice_name, rate=PARALLEA_TTS_RATE)
            await communicator.save(str(tmp_audio))
            if not tmp_audio.exists() or tmp_audio.stat().st_size <= 0:
                raise RuntimeError("Edge TTS returned an empty audio file.")
            tmp_audio.replace(out_path)
            return out_path
        except Exception as exc:
            errors.append(f"{voice_name}: {exc}")
        finally:
            tmp_audio.unlink(missing_ok=True)
    raise RuntimeError("Edge TTS synthesis failed. " + " | ".join(errors))


async def speak_text(
    session_id: str,
    text: str,
    voice_id: str,
    lang: str = "en-us",
    fallback_voice: str | None = None,
) -> dict:
    filename = f"{session_id}_{uuid.uuid4().hex}{TTS_AUDIO_EXTENSION}"
    out_path = AUDIO_DIR / filename
    await synthesize_to_file(text=text, voice_id=voice_id, lang=lang, out_path=out_path, fallback_voice=fallback_voice)
    return {"filename": filename, "audio_url": f"/audio-response/{filename}"}


async def speak_cached(
    cache_key: str,
    text: str,
    voice_id: str,
    lang: str = "en-us",
    fallback_voice: str | None = None,
) -> dict:
    safe_key = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in cache_key).strip("_") or "cached_audio"
    filename = f"{safe_key}{TTS_AUDIO_EXTENSION}"
    out_path = AUDIO_DIR / filename
    if not out_path.exists() or out_path.stat().st_size <= 0:
        await synthesize_to_file(text=text, voice_id=voice_id, lang=lang, out_path=out_path, fallback_voice=fallback_voice)
    return {"filename": filename, "audio_url": f"/audio-response/{filename}"}


async def speak_segments(
    session_id: str,
    segments: list[dict],
    voice_id: str,
    lang: str = "en-us",
    fallback_voice: str | None = None,
) -> list[dict]:
    results = []
    for idx, segment in enumerate(segments or [], start=1):
        if not isinstance(segment, dict):
            continue
        text = (segment.get("speech_text") or segment.get("text") or "").strip()
        if not text:
            continue
        segment_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(segment.get("segment_id") or f"segment_{idx}"))
        filename = f"{session_id}_{segment_id}_{uuid.uuid4().hex}{TTS_AUDIO_EXTENSION}"
        out_path = AUDIO_DIR / filename
        await synthesize_to_file(text=text, voice_id=voice_id, lang=lang, out_path=out_path, fallback_voice=fallback_voice)
        results.append(
            {
                "segment_id": segment.get("segment_id") or f"segment_{idx}",
                "text": text,
                "audio_url": f"/audio-response/{filename}",
            }
        )
    return results


def transcribe_question_result(audio_path: str, *, original_mime_type: str = "", original_filename: str = "") -> dict[str, Any]:
    source = Path(audio_path)
    size_bytes = source.stat().st_size if source.exists() else 0
    source_duration = audio_duration_seconds(str(source))
    provider = _effective_stt_provider()
    wav_path = ""
    remove_wav = False
    normalized_duration = None
    try:
        # VAD has already decided the turn boundary. STT receives one complete
        # utterance here; normalize browser audio before decoding it.
        wav_path, remove_wav, normalized_duration = _prepare_wav_for_stt(str(source), original_mime_type=original_mime_type)
        if provider == "openai":
            payload = _transcribe_with_openai(wav_path)
        else:
            payload = _transcribe_with_local_whisper(wav_path)
    finally:
        if remove_wav and wav_path:
            Path(wav_path).unlink(missing_ok=True)

    text = (payload.get("text") or "").strip()
    reason = _unclear_reason(
        text,
        confidence=payload.get("confidence") if isinstance(payload.get("confidence"), (int, float)) else None,
        no_speech_prob=payload.get("no_speech_prob") if isinstance(payload.get("no_speech_prob"), (int, float)) else None,
        duration_sec=normalized_duration or source_duration,
        size_bytes=size_bytes,
    )
    return {
        "text": text,
        "provider": payload.get("provider") or provider,
        "model": payload.get("model") or _effective_stt_model(provider),
        "language": payload.get("language") or STT_LANGUAGE,
        "confidence": payload.get("confidence"),
        "avg_logprob": payload.get("avg_logprob"),
        "no_speech_prob": payload.get("no_speech_prob"),
        "segments": payload.get("segments"),
        "needs_confirmation": bool(reason),
        "unclear_reason": reason,
        "audio": {
            "original_filename": original_filename,
            "original_mime_type": original_mime_type,
            "size_bytes": size_bytes,
            "source_duration_sec": source_duration,
            "normalized_duration_sec": normalized_duration,
            "normalized_format": "wav/mono/16000",
        },
    }


def transcribe_question(audio_path: str) -> str:
    return transcribe_question_result(audio_path).get("text", "")
