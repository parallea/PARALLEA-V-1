from __future__ import annotations

import logging
import os
from typing import Any


logger = logging.getLogger("parallea.models")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_PROVIDER = "gemini"
GROQ_PROVIDER = "groq"

_GROQ_MODEL_MARKERS = (
    "llama",
    "mixtral",
    "versatile",
    "qwen",
    "deepseek",
    "meta-llama",
    "openai/",
    "moonshot",
)


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def normalize_model_name(model: Any) -> str:
    value = clean_spaces(model).strip().strip("/")
    if value.lower().startswith("models/"):
        value = value.split("/", 1)[1]
    return value


def detect_model_provider(model: Any) -> str:
    value = normalize_model_name(model).lower()
    if not value:
        return "unknown"
    if "gemini" in value:
        return GEMINI_PROVIDER
    if any(marker in value for marker in _GROQ_MODEL_MARKERS):
        return GROQ_PROVIDER
    return "unknown"


def is_model_compatible_with_provider(provider: str, model: Any) -> bool:
    detected = detect_model_provider(model)
    if provider == GEMINI_PROVIDER:
        return detected == GEMINI_PROVIDER
    if provider == GROQ_PROVIDER:
        return detected == GROQ_PROVIDER
    return False


def resolve_gemini_model_config(
    primary_env: str,
    *,
    fallback_envs: list[str] | None = None,
    default: str = DEFAULT_GEMINI_MODEL,
    label: str = "gemini",
) -> dict[str, str]:
    candidates = [primary_env, *(fallback_envs or [])]
    normalized_default = normalize_model_name(default) or DEFAULT_GEMINI_MODEL
    for env_name in candidates:
        raw = os.getenv(env_name, "")
        candidate = normalize_model_name(raw)
        if not candidate:
            continue
        if is_model_compatible_with_provider(GEMINI_PROVIDER, candidate):
            return {
                "provider": GEMINI_PROVIDER,
                "model": candidate,
                "source": env_name,
            }
        logger.warning(
            "model-routing ignored incompatible config label=%s provider=%s env=%s model=%s detected_provider=%s fallback=%s",
            label,
            GEMINI_PROVIDER,
            env_name,
            candidate,
            detect_model_provider(candidate),
            normalized_default,
        )
    return {
        "provider": GEMINI_PROVIDER,
        "model": normalized_default,
        "source": "default",
    }


def resolve_groq_model_config(
    primary_env: str,
    *,
    fallback_envs: list[str] | None = None,
    default: str = DEFAULT_GROQ_MODEL,
    label: str = "groq",
) -> dict[str, str]:
    candidates = [primary_env, *(fallback_envs or [])]
    normalized_default = normalize_model_name(default) or DEFAULT_GROQ_MODEL
    for env_name in candidates:
        raw = os.getenv(env_name, "")
        candidate = normalize_model_name(raw)
        if not candidate:
            continue
        if is_model_compatible_with_provider(GROQ_PROVIDER, candidate):
            return {
                "provider": GROQ_PROVIDER,
                "model": candidate,
                "source": env_name,
            }
        logger.warning(
            "model-routing ignored incompatible config label=%s provider=%s env=%s model=%s detected_provider=%s fallback=%s",
            label,
            GROQ_PROVIDER,
            env_name,
            candidate,
            detect_model_provider(candidate),
            normalized_default,
        )
    return {
        "provider": GROQ_PROVIDER,
        "model": normalized_default,
        "source": "default",
    }
