from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from config import (
    ALLOW_MODEL_FALLBACK,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    OPENAI_API_KEY,
    PARALLEA_DEFAULT_MODEL,
    PARALLEA_DEFAULT_PROVIDER,
)

logger = logging.getLogger("parallea.model-router")

TASKS = {
    "default": ("PARALLEA_DEFAULT_PROVIDER", "PARALLEA_DEFAULT_MODEL"),
    "persona": ("PARALLEA_PERSONA_PROVIDER", "PARALLEA_PERSONA_MODEL"),
    "roadmap": ("PARALLEA_ROADMAP_PROVIDER", "PARALLEA_ROADMAP_MODEL"),
    "answer": ("PARALLEA_ANSWER_PROVIDER", "PARALLEA_ANSWER_MODEL"),
    "clarification": ("PARALLEA_CLARIFICATION_PROVIDER", "PARALLEA_CLARIFICATION_MODEL"),
    "visual": ("PARALLEA_VISUAL_PROVIDER", "PARALLEA_VISUAL_MODEL"),
    "teaching_pipeline": ("PARALLEA_TEACHING_PIPELINE_PROVIDER", "PARALLEA_TEACHING_PIPELINE_MODEL"),
    "topic_router": ("PARALLEA_TOPIC_ROUTER_PROVIDER", "PARALLEA_TOPIC_ROUTER_MODEL"),
    "scene_planner": ("PARALLEA_SCENE_PLANNER_PROVIDER", "PARALLEA_SCENE_PLANNER_MODEL"),
}

TASK_DEFAULTS = {
    "default": ("openai", "gpt-5.4-mini"),
    "persona": ("openai", "gpt-5.4-mini"),
    "roadmap": ("openai", "gpt-5.4-mini"),
    "answer": ("openai", "gpt-5.4-mini"),
    "clarification": ("openai", "gpt-5.4-mini"),
    "visual": ("openai", "gpt-5.4-mini"),
    "teaching_pipeline": ("openai", "gpt-5.4-mini"),
    "topic_router": ("openai", "gpt-5.4-mini"),
    "scene_planner": ("openai", "gpt-5.4-mini"),
}

PROVIDERS = {"openai", "gemini", "groq", "stub", "mock"}


@dataclass(frozen=True)
class ModelConfig:
    task: str
    provider: str
    model: str
    source: str
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "fallback_used": self.fallback_used,
        }


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def normalize_provider(provider: Any) -> str:
    value = clean_spaces(provider).lower()
    return value if value in PROVIDERS else "stub"


def normalize_model_name(model: Any) -> str:
    value = clean_spaces(model).strip().strip("/")
    if value.lower().startswith("models/"):
        value = value.split("/", 1)[1]
    return value


def detect_model_provider(model: Any) -> str:
    value = normalize_model_name(model).lower()
    if not value:
        return "unknown"
    if value.startswith("gemini-") or "gemini" in value:
        return "gemini"
    if value.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if any(marker in value for marker in ("llama", "mixtral", "gemma", "qwen", "deepseek", "versatile", "meta-llama", "moonshot")):
        return "groq"
    return "unknown"


def model_compatible(provider: str, model: str) -> bool:
    if provider in {"stub", "mock"}:
        return True
    detected = detect_model_provider(model)
    return detected in {provider, "unknown"}


def _configured_default() -> tuple[str, str]:
    provider = normalize_provider(PARALLEA_DEFAULT_PROVIDER) or "openai"
    model = normalize_model_name(PARALLEA_DEFAULT_MODEL) or TASK_DEFAULTS["default"][1]
    if provider in {"stub", "mock"} or model_compatible(provider, model):
        return provider, model
    return TASK_DEFAULTS["default"]


def get_model_config(task: str) -> ModelConfig:
    normalized_task = clean_spaces(task).lower() or "default"
    if normalized_task not in TASKS:
        normalized_task = "default"
    provider_env, model_env = TASKS[normalized_task]
    task_default_provider, task_default_model = TASK_DEFAULTS.get(normalized_task, _configured_default())
    fallback_provider, fallback_model = _configured_default()
    configured_provider = clean_spaces(os.getenv(provider_env, "")).lower()
    configured_model = normalize_model_name(os.getenv(model_env, ""))
    model = normalize_model_name(configured_model or task_default_model or fallback_model)
    if configured_provider:
        provider = normalize_provider(configured_provider)
    elif configured_model:
        inferred_provider = detect_model_provider(configured_model)
        provider = inferred_provider if inferred_provider in PROVIDERS - {"stub", "mock"} else normalize_provider(task_default_provider or fallback_provider)
    else:
        provider = normalize_provider(task_default_provider or fallback_provider)
    source = f"{provider_env}/{model_env}"
    if provider in {"stub", "mock"}:
        cfg = ModelConfig(normalized_task, "stub", model or "stub", source)
        logger.info("[model-router] task=%s provider=%s model=%s", cfg.task, cfg.provider, cfg.model)
        return cfg
    if model_compatible(provider, model):
        cfg = ModelConfig(normalized_task, provider, model, source)
        logger.info("[model-router] task=%s provider=%s model=%s", cfg.task, cfg.provider, cfg.model)
        return cfg

    detected = detect_model_provider(model)
    message = (
        f"Incompatible config: task={normalized_task} provider={provider} "
        f"model={model} detected_provider={detected}"
    )
    if not ALLOW_MODEL_FALLBACK:
        logger.error("[model-router] %s", message)
        raise RuntimeError(message)
    if model_compatible(task_default_provider, task_default_model):
        cfg = ModelConfig(normalized_task, task_default_provider, task_default_model, "task-default", True)
    else:
        cfg = ModelConfig(normalized_task, fallback_provider, fallback_model, "global-default", True)
    logger.warning("[model-router] %s fallback_provider=%s fallback_model=%s", message, cfg.provider, cfg.model)
    logger.info("[model-router] task=%s provider=%s model=%s", cfg.task, cfg.provider, cfg.model)
    return cfg


def openai_uses_completion_tokens(model: str) -> bool:
    return normalize_model_name(model).lower().startswith(("gpt-5", "o1", "o3", "o4"))


def openai_supports_temperature(model: str) -> bool:
    return not openai_uses_completion_tokens(model)


def build_openai_params(model: str, max_tokens: int, temperature: float) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if openai_uses_completion_tokens(model):
        params["max_completion_tokens"] = max_tokens
    else:
        params["max_tokens"] = max_tokens
    if openai_supports_temperature(model):
        params["temperature"] = temperature
    return params


def strip_json_fences(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = strip_json_fences(str(raw or ""))
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}


async def _call_openai_json(cfg: ModelConfig, system_prompt: str, user_prompt: str, *, max_tokens: int, temperature: float) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    params = build_openai_params(cfg.model, max_tokens, temperature)
    logger.info(
        "[model-router] openai request task=%s model=%s token_param=%s temperature_passed=%s",
        cfg.task,
        cfg.model,
        "max_completion_tokens" if "max_completion_tokens" in params else "max_tokens",
        "temperature" in params,
    )
    response = await client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        **params,
    )
    return response.choices[0].message.content or ""


async def _call_gemini_json(cfg: ModelConfig, system_prompt: str, user_prompt: str, *, max_tokens: int, temperature: float) -> str:
    from gemini_service import build_gemini_client, generate_json_with_retry

    client = build_gemini_client(GEMINI_API_KEY, enabled=bool(GEMINI_API_KEY))
    if not client:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    return await generate_json_with_retry(
        client,
        model=cfg.model,
        prompt=user_prompt,
        system_instruction=system_prompt,
        temperature=temperature,
        max_output_tokens=max_tokens,
        operation=f"llm-json:{cfg.task}",
        logger=logger,
    )


async def _call_groq_json(cfg: ModelConfig, system_prompt: str, user_prompt: str, *, max_tokens: int, temperature: float) -> str:
    from groq_service import build_async_groq_client, chat_completion_json_with_retry

    client = build_async_groq_client(GROQ_API_KEY, enabled=bool(GROQ_API_KEY))
    if not client:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    return await chat_completion_json_with_retry(
        client,
        model=cfg.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        operation=f"llm-json:{cfg.task}",
        logger=logger,
    )


async def llm_json(
    task: str,
    system_prompt: str,
    user_prompt: str,
    *,
    schema: dict[str, Any] | None = None,  # reserved for providers that support schemas
    max_tokens: int = 4500,
    temperature: float = 0.3,
) -> dict[str, Any]:
    del schema
    cfg = get_model_config(task)
    logger.info(
        "[model-router] llm_json task=%s provider=%s model=%s system_chars=%s user_chars=%s",
        cfg.task,
        cfg.provider,
        cfg.model,
        len(system_prompt or ""),
        len(user_prompt or ""),
    )
    if cfg.provider == "stub":
        return {}
    if cfg.provider == "openai":
        raw = await _call_openai_json(cfg, system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature)
    elif cfg.provider == "gemini":
        raw = await _call_gemini_json(cfg, system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature)
    elif cfg.provider == "groq":
        raw = await _call_groq_json(cfg, system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature)
    else:
        raise RuntimeError(f"Unsupported LLM provider for task={task}: {cfg.provider}")
    parsed = parse_json_response(raw)
    logger.info("[model-router] llm_json parsed task=%s provider=%s model=%s ok=%s", cfg.task, cfg.provider, cfg.model, bool(parsed))
    return parsed
