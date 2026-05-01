from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import urllib.error
import urllib.request
from typing import Any

from model_routing import GEMINI_PROVIDER, detect_model_provider, is_model_compatible_with_provider, normalize_model_name


GEMINI_MAX_RETRIES = max(0, int(os.getenv("PARALLEA_GEMINI_MAX_RETRIES", "3") or "3"))
GEMINI_BACKOFF_BASE_SEC = max(0.5, float(os.getenv("PARALLEA_GEMINI_BACKOFF_BASE_SEC", "2.0") or "2.0"))
GEMINI_BACKOFF_MAX_SEC = max(GEMINI_BACKOFF_BASE_SEC, float(os.getenv("PARALLEA_GEMINI_BACKOFF_MAX_SEC", "20.0") or "20.0"))
DEFAULT_GEMINI_BASE_URL = (os.getenv("PARALLEA_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com") or "https://generativelanguage.googleapis.com").rstrip("/")
DEFAULT_GEMINI_API_VERSION = (os.getenv("PARALLEA_GEMINI_API_VERSION", "v1beta") or "v1beta").strip().strip("/")


def build_gemini_client(api_key: str, enabled: bool = True) -> dict[str, str] | None:
    if not enabled or not api_key:
        return None
    return {
        "provider": GEMINI_PROVIDER,
        "api_key": api_key,
        "base_url": DEFAULT_GEMINI_BASE_URL,
        "api_version": DEFAULT_GEMINI_API_VERSION,
    }


def build_generate_content_endpoint(client: dict[str, str] | None, model: str, *, include_key: bool = False) -> str:
    if not client:
        raise RuntimeError("Gemini client is not configured.")
    normalized_model = normalize_model_name(model)
    endpoint = f"{client['base_url']}/{client['api_version']}/models/{normalized_model}:generateContent"
    if include_key:
        endpoint = f"{endpoint}?key={client['api_key']}"
    return endpoint


def validate_gemini_model(model: str) -> str:
    normalized_model = normalize_model_name(model)
    if not normalized_model:
        raise ValueError("Gemini service requires a non-empty model name.")
    if not is_model_compatible_with_provider(GEMINI_PROVIDER, normalized_model):
        raise ValueError(
            "Gemini service received an incompatible model "
            f"'{normalized_model}' (detected provider={detect_model_provider(normalized_model)}). "
            "Use a Gemini model such as 'gemini-2.5-flash' in PARALLEA_GEMINI_* settings."
        )
    return normalized_model


def exception_status_code(exc: Exception) -> int | None:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code
    return None


def is_retryable_error(exc: Exception) -> bool:
    status = exception_status_code(exc)
    if status in {408, 429, 500, 502, 503, 504}:
        return True
    message = str(exc).lower()
    return "rate limit" in message or "temporarily unavailable" in message or "timeout" in message


def parse_retry_delay_seconds(exc: Exception) -> float | None:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                value = float(retry_after)
                if value > 0:
                    return value
            except Exception:
                pass
    message = str(exc)
    for pattern in [
        r"try again in ([0-9]+(?:\.[0-9]+)?)s",
        r"retry after ([0-9]+(?:\.[0-9]+)?)s",
        r"please try again in ([0-9]+(?:\.[0-9]+)?)s",
    ]:
        match = re.search(pattern, message, flags=re.I)
        if match:
            try:
                value = float(match.group(1))
                if value > 0:
                    return value
            except Exception:
                continue
    return None


def bounded_backoff_seconds(attempt: int, exc: Exception) -> float:
    retry_after = parse_retry_delay_seconds(exc)
    if retry_after is not None:
        return min(GEMINI_BACKOFF_MAX_SEC, max(0.5, retry_after))
    expo = GEMINI_BACKOFF_BASE_SEC * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0.0, 0.35 * GEMINI_BACKOFF_BASE_SEC)
    return min(GEMINI_BACKOFF_MAX_SEC, expo + jitter)


def normalize_messages(messages: list[dict[str, str]] | None, prompt: str | None = None) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages or []:
        role = str(message.get("role") or "").strip().lower()
        text = str(message.get("content") or "").strip()
        if not text:
            continue
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    if prompt and not contents:
        contents.append({"role": "user", "parts": [{"text": prompt}]})
    return contents


def extract_text_from_response(data: dict[str, Any]) -> str:
    for candidate in data.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if text.strip():
            return text.strip()
    return ""


def generate_json_sync(
    client: dict[str, str] | None,
    *,
    model: str,
    prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    system_instruction: str | None = None,
    response_schema: dict[str, Any] | None = None,
    temperature: float = 0.3,
    max_output_tokens: int = 900,
) -> str:
    if not client:
        raise RuntimeError("Gemini client is not configured.")
    normalized_model = validate_gemini_model(model)
    endpoint = build_generate_content_endpoint(client, normalized_model, include_key=True)
    contents = normalize_messages(messages, prompt=prompt)
    if not contents:
        raise ValueError("Gemini request requires either prompt text or messages.")
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if response_schema:
        payload["generationConfig"]["responseSchema"] = response_schema
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return extract_text_from_response(data)


async def generate_json_with_retry(
    client: dict[str, str] | None,
    *,
    model: str,
    prompt: str | None = None,
    messages: list[dict[str, str]] | None = None,
    system_instruction: str | None = None,
    response_schema: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
    operation: str = "gemini-call",
    temperature: float = 0.3,
    max_output_tokens: int = 900,
) -> str:
    if not client:
        raise RuntimeError("Gemini client is not configured.")
    active_logger = logger or logging.getLogger("parallea.gemini")
    normalized_model = validate_gemini_model(model)
    safe_endpoint = build_generate_content_endpoint(client, normalized_model, include_key=False)
    active_logger.info(
        "gemini request operation=%s provider=%s model=%s endpoint=%s has_messages=%s has_schema=%s",
        operation,
        client.get("provider", GEMINI_PROVIDER),
        normalized_model,
        safe_endpoint,
        bool(messages),
        bool(response_schema),
    )
    last_exc: Exception | None = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 2):
        try:
            return await asyncio.to_thread(
                generate_json_sync,
                client,
                model=normalized_model,
                prompt=prompt,
                messages=messages,
                system_instruction=system_instruction,
                response_schema=response_schema,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            last_exc = exc
            if is_retryable_error(exc) and attempt <= GEMINI_MAX_RETRIES:
                delay = bounded_backoff_seconds(attempt, exc)
                active_logger.warning(
                    "gemini retry operation=%s attempt=%s/%s delay_sec=%.2f status=%s error=%s",
                    operation,
                    attempt,
                    GEMINI_MAX_RETRIES + 1,
                    delay,
                    exception_status_code(exc),
                    str(exc),
                )
                await asyncio.sleep(delay)
                continue
            active_logger.error(
                "gemini request failed operation=%s provider=%s model=%s endpoint=%s status=%s error=%s",
                operation,
                client.get("provider", GEMINI_PROVIDER),
                normalized_model,
                safe_endpoint,
                exception_status_code(exc),
                str(exc),
            )
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Gemini request failed without a captured exception for operation={operation}.")
