from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import ASSEMBLYAI_API_KEY

try:
    import assemblyai as aai
except Exception:
    aai = None

try:
    import whisper
except Exception:
    whisper = None

if aai and ASSEMBLYAI_API_KEY:
    aai.settings.api_key = ASSEMBLYAI_API_KEY


def _extract_audio(video_path: str) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", wav_path]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return wav_path


def _assemblyai_chunks(wav_path: str, interval: int = 10):
    if not (aai and ASSEMBLYAI_API_KEY):
        return None
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
    return chunks


_whisper_model = None
def _get_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    if whisper is None:
        return None
    print("Loading Whisper tiny for upload transcription...")
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
    wav_path = _extract_audio(video_path)
    try:
        chunks = None
        if aai and ASSEMBLYAI_API_KEY:
            chunks = _assemblyai_chunks(wav_path, interval=interval)
        if not chunks:
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
