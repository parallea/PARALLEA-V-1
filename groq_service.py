from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any

try:
    from groq import AsyncGroq
except Exception:
    AsyncGroq = None


GROQ_MAX_RETRIES = max(0, int(os.getenv("PARALLEA_GROQ_MAX_RETRIES", "3") or "3"))
GROQ_BACKOFF_BASE_SEC = max(0.5, float(os.getenv("PARALLEA_GROQ_BACKOFF_BASE_SEC", "2.0") or "2.0"))
GROQ_BACKOFF_MAX_SEC = max(GROQ_BACKOFF_BASE_SEC, float(os.getenv("PARALLEA_GROQ_BACKOFF_MAX_SEC", "20.0") or "20.0"))


def build_async_groq_client(api_key: str, enabled: bool = True) -> Any:
    if not enabled or not api_key or not AsyncGroq:
        return None
    return AsyncGroq(api_key=api_key)


def exception_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def exception_headers(exc: Exception) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(key).lower(): str(value) for key, value in headers.items()}
    except Exception:
        return {}


def is_rate_limit_error(exc: Exception) -> bool:
    status = exception_status_code(exc)
    if status == 429:
        return True
    message = str(exc).lower()
    return "rate_limit_exceeded" in message or "rate limit" in message or "429" in message


def parse_retry_delay_seconds(exc: Exception) -> float | None:
    headers = exception_headers(exc)
    for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        raw = headers.get(key)
        if not raw:
            continue
        try:
            value = float(raw)
            if value > 0:
                return value
        except Exception:
            continue
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
        return min(GROQ_BACKOFF_MAX_SEC, max(0.5, retry_after))
    expo = GROQ_BACKOFF_BASE_SEC * (2 ** max(0, attempt - 1))
    jitter = random.uniform(0.0, 0.35 * GROQ_BACKOFF_BASE_SEC)
    return min(GROQ_BACKOFF_MAX_SEC, expo + jitter)


async def chat_completion_json_with_retry(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
    logger: logging.Logger | None = None,
    operation: str = "groq-call",
    temperature: float = 0.35,
    max_tokens: int = 900,
    response_format: dict[str, str] | None = None,
) -> str:
    if not client:
        raise RuntimeError("Groq client is not configured.")
    active_logger = logger or logging.getLogger("parallea.groq")
    last_exc: Exception | None = None
    for attempt in range(1, GROQ_MAX_RETRIES + 2):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format or {"type": "json_object"},
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            last_exc = exc
            if is_rate_limit_error(exc) and attempt <= GROQ_MAX_RETRIES + 1:
                if attempt > GROQ_MAX_RETRIES:
                    break
                delay = bounded_backoff_seconds(attempt, exc)
                active_logger.warning(
                    "groq retry operation=%s attempt=%s/%s delay_sec=%.2f status=%s error=%s",
                    operation,
                    attempt,
                    GROQ_MAX_RETRIES + 1,
                    delay,
                    exception_status_code(exc),
                    str(exc),
                )
                await asyncio.sleep(delay)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Groq request failed without a captured exception for operation={operation}.")
