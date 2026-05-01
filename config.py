from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw or default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_choice(name: str, default: str, choices: set[str]) -> str:
    raw = env_str(name, default).strip().lower()
    if raw in {"true", "yes", "on"}:
        raw = "1"
    elif raw in {"false", "no", "off"}:
        raw = "0"
    return raw if raw in choices else default


def env_path(name: str, default: Path, base: Path) -> Path:
    raw = env_str(name, "")
    if not raw:
        return default
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def default_external_runtime_root(app_name: str = "Parallea", project_base: Path | None = None) -> Path:
    base = project_base or Path.cwd()
    candidates = []

    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / app_name)

    xdg_cache = os.getenv("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        candidates.append(Path(xdg_cache) / app_name.lower())

    candidates.append(Path.home() / ".cache" / app_name.lower())
    candidates.append(Path.home() / ".codex" / "memories" / app_name.lower())
    candidates.append(base.parent / f".{app_name.lower()}_runtime")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / f".write_probe_{os.getpid()}"
            probe.mkdir(exist_ok=True)
            probe.rmdir()
            return candidate.resolve()
        except Exception:
            continue

    return (base / f".{app_name.lower()}_runtime").resolve()


BASE_DIR = Path(__file__).resolve().parent
load_local_env(BASE_DIR / ".env")

DEFAULT_RUNTIME_ROOT = default_external_runtime_root("Parallea", BASE_DIR)
DATA_DIR = env_path("DATA_DIR", env_path("PARALLEA_DATA_DIR", BASE_DIR / "data", BASE_DIR), BASE_DIR)
TMP_DIR = env_path("TMP_DIR", env_path("PARALLEA_TMP_DIR", DATA_DIR / "tmp", BASE_DIR), BASE_DIR)
UPLOADS_DIR = env_path("PARALLEA_UPLOADS_DIR", BASE_DIR / "uploads", BASE_DIR)
THUMBNAILS_DIR = env_path("PARALLEA_THUMBNAILS_DIR", BASE_DIR / "thumbnails", BASE_DIR)
AUDIO_DIR = env_path("PARALLEA_AUDIO_DIR", DEFAULT_RUNTIME_ROOT / "audio", BASE_DIR)
SESSIONS_DIR = env_path("PARALLEA_SESSIONS_DIR", DATA_DIR / "sessions", DATA_DIR)
VIDEOS_DB = env_path("PARALLEA_VIDEOS_DB", DATA_DIR / "videos.json", DATA_DIR)
RENDERS_DIR = env_path("PARALLEA_RENDERS_DIR", DATA_DIR / "renders", DATA_DIR)
DEFAULT_MANIM_RUNTIME_DIR = DATA_DIR / "manim_runtime"
MANIM_RUNTIME_DIR = env_path(
    "MANIM_RUNTIME_DIR",
    env_path("PARALLEA_MANIM_RUNTIME_DIR", DEFAULT_MANIM_RUNTIME_DIR, BASE_DIR),
    BASE_DIR,
)
MANIM_DEBUG_DIR = env_path(
    "MANIM_DEBUG_DIR",
    env_path("PARALLEA_MANIM_DEBUG_DIR", MANIM_RUNTIME_DIR / "debug", BASE_DIR),
    BASE_DIR,
)
PUBLIC_DIR = env_path("PARALLEA_PUBLIC_DIR", BASE_DIR / "public", BASE_DIR)
MANIM_PUBLIC_OUTPUT_DIR = env_path(
    "MANIM_OUTPUT_DIR",
    env_path("PARALLEA_MANIM_OUTPUT_DIR", RENDERS_DIR / "manim", BASE_DIR),
    BASE_DIR,
)
MANIM_PUBLIC_BASE_URL = env_str("MANIM_PUBLIC_BASE_URL", "/rendered-scenes/manim").rstrip("/") or "/rendered-scenes/manim"
MANIM_QUALITY = env_str("MANIM_QUALITY", "ql").strip().lstrip("-") or "ql"
MANIM_RENDER_TIMEOUT_SECONDS = max(30, env_int("MANIM_RENDER_TIMEOUT_SECONDS", env_int("PARALLEA_MANIM_RENDER_TIMEOUT_SEC", 120)))
MANIM_ENABLED = env_bool("MANIM_ENABLED", True)
MANIM_FORCE_TEXT_ONLY = env_bool("MANIM_FORCE_TEXT_ONLY", False)
MANIM_ALLOW_MATHTEX = env_choice("MANIM_ALLOW_MATHTEX", "auto", {"auto", "0", "1"})
MANIM_REQUIRE_LATEX = env_bool("MANIM_REQUIRE_LATEX", False)
MANIM_VISUAL_STYLE = env_choice("MANIM_VISUAL_STYLE", "creative_safe", {"creative_safe", "strict_layout", "fallback_only"})
ROADMAP_PART_MATCH_THRESHOLD = min(1.0, max(0.0, env_float("ROADMAP_PART_MATCH_THRESHOLD", 0.45)))

for d in [DATA_DIR, TMP_DIR, UPLOADS_DIR, THUMBNAILS_DIR, AUDIO_DIR, SESSIONS_DIR, RENDERS_DIR, MANIM_RUNTIME_DIR, MANIM_DEBUG_DIR, PUBLIC_DIR, MANIM_PUBLIC_OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

ASSEMBLYAI_API_KEY = env_str("ASSEMBLYAI_API_KEY")
GROQ_API_KEY = env_str("GROQ_API_KEY")
GEMINI_API_KEY = env_str("GEMINI_API_KEY")
OPENAI_API_KEY = env_str("OPENAI_API_KEY")
TEACHER_TRANSCRIPTION_PROVIDER = env_str("TEACHER_TRANSCRIPTION_PROVIDER").strip().lower()
TRANSCRIPTION_PROVIDER = env_str("TRANSCRIPTION_PROVIDER").strip().lower()
STORAGE_BACKEND = env_choice("STORAGE_BACKEND", "local", {"local", "s3"})
S3_PROVIDER = env_str("S3_PROVIDER", "generic").strip().lower() or "generic"
S3_BUCKET_NAME = env_str("S3_BUCKET_NAME")
S3_ENDPOINT_URL = env_str("S3_ENDPOINT_URL", env_str("AWS_ENDPOINT_URL"))
S3_REGION = env_str("S3_REGION", env_str("AWS_REGION", "auto")) or "auto"
S3_ACCESS_KEY_ID = env_str("S3_ACCESS_KEY_ID", env_str("AWS_ACCESS_KEY_ID"))
S3_SECRET_ACCESS_KEY = env_str("S3_SECRET_ACCESS_KEY", env_str("AWS_SECRET_ACCESS_KEY"))
S3_PUBLIC_BASE_URL = env_str("S3_PUBLIC_BASE_URL").rstrip("/")
S3_PRESIGNED_URL_EXPIRES_SECONDS = max(60, env_int("S3_PRESIGNED_URL_EXPIRES_SECONDS", 3600))
SUPABASE_URL = env_str("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = env_str("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_EMAILS = env_str("ADMIN_EMAILS")
ADMIN_SECRET = env_str("ADMIN_SECRET")
PARALLEA_OPENAI_PIPELINE_MODEL = env_str("PARALLEA_OPENAI_PIPELINE_MODEL", "gpt-5.4-mini")
PARALLEA_DEFAULT_PROVIDER = env_str("PARALLEA_DEFAULT_PROVIDER", "openai")
PARALLEA_DEFAULT_MODEL = env_str("PARALLEA_DEFAULT_MODEL", "gpt-5.4-mini")
ALLOW_MODEL_FALLBACK = env_bool("ALLOW_MODEL_FALLBACK", True)

# Student microphone transcription. VAD only decides when an utterance starts
# and ends; this STT config controls how the completed audio chunk is decoded.
STT_PROVIDER = env_str("STT_PROVIDER", "whisper").strip().lower() or "whisper"
STT_MODEL = env_str("STT_MODEL", "").strip()
STT_LANGUAGE = env_str("STT_LANGUAGE", "en").strip().lower() or "en"

PARALLEA_DEFAULT_VOICE_ID = env_str("PARALLEA_DEFAULT_VOICE_ID", "en-US-JennyNeural")
PARALLEA_TTS_RATE = env_str("PARALLEA_TTS_RATE", "+0%").strip() or "+0%"
TTS_AUDIO_EXTENSION = ".mp3"
GENERATED_MEDIA_TTL_SECONDS = max(60, env_int("GENERATED_MEDIA_TTL_SECONDS", 3600))
DELETE_GENERATED_MEDIA_AFTER_PLAYBACK = env_bool("DELETE_GENERATED_MEDIA_AFTER_PLAYBACK", True)
MAX_MANIM_VISUAL_DURATION_SECONDS = max(20, env_int("MAX_MANIM_VISUAL_DURATION_SECONDS", 90))
MIN_VISUAL_TO_AUDIO_DURATION_RATIO = min(1.0, max(0.1, env_float("MIN_VISUAL_TO_AUDIO_DURATION_RATIO", 0.75)))


def avatar_voice_id(env_name: str, default_voice: str | None = None) -> str:
    return env_str(env_name, default_voice or PARALLEA_DEFAULT_VOICE_ID)


def avatar_preset(
    avatar_id: str,
    name: str,
    voice_label: str,
    voice_env: str,
    default_voice: str,
    edge_voice: str,
    skin: str,
    hair: str,
    shirt: str,
    accent: str,
    eye: str,
) -> dict:
    return {
        "id": avatar_id,
        "name": name,
        "voice": voice_label,
        "voice_id": avatar_voice_id(voice_env, default_voice),
        "edge_voice": edge_voice,
        "lang": "en-us",
        "style": {
            "skin": skin,
            "hair": hair,
            "shirt": shirt,
            "accent": accent,
            "eye": eye,
        },
    }


AVATAR_PRESETS = [
    avatar_preset("girl_1", "Ava", "Jenny", "PARALLEA_VOICE_AVA_ID", "en-US-JennyNeural", "en-US-JennyNeural", "#f1c7a3", "#2a201d", "#ec6d2f", "#7dd3fc", "#1c1c1c"),
    avatar_preset("girl_2", "Mia", "Sonia", "PARALLEA_VOICE_MIA_ID", "en-GB-SoniaNeural", "en-GB-SoniaNeural", "#efc5b0", "#5b2d16", "#8b5cf6", "#c4b5fd", "#151515"),
    avatar_preset("girl_3", "Zara", "Natasha", "PARALLEA_VOICE_ZARA_ID", "en-AU-NatashaNeural", "en-AU-NatashaNeural", "#d89f7c", "#101417", "#10b981", "#99f6e4", "#111111"),
    avatar_preset("girl_4", "Lina", "Aria", "PARALLEA_VOICE_LINA_ID", "en-US-AriaNeural", "en-US-AriaNeural", "#f3cfb8", "#c2410c", "#e11d48", "#fda4af", "#111111"),
    avatar_preset("man_1", "Noah", "Guy", "PARALLEA_VOICE_NOAH_ID", "en-US-GuyNeural", "en-US-GuyNeural", "#c58a67", "#111827", "#2563eb", "#93c5fd", "#0f0f0f"),
    avatar_preset("man_2", "Arin", "Ryan", "PARALLEA_VOICE_ARIN_ID", "en-GB-RyanNeural", "en-GB-RyanNeural", "#b97f58", "#44261c", "#f59e0b", "#fde68a", "#0f0f0f"),
]

DEFAULT_AVATAR_ID = AVATAR_PRESETS[0]["id"]
MAX_HISTORY = 40
MAX_NOTES = 200

VISUAL_PIPELINE = env_str("PARALLEA_VISUAL_PIPELINE", "unified").strip().lower() or "unified"
if VISUAL_PIPELINE not in {"unified", "legacy"}:
    VISUAL_PIPELINE = "unified"
