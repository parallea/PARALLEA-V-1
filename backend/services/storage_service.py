from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import (
    DATA_DIR,
    MANIM_RUNTIME_DIR,
    S3_ACCESS_KEY_ID,
    S3_BUCKET_NAME,
    S3_ENDPOINT_URL,
    S3_PRESIGNED_URL_EXPIRES_SECONDS,
    S3_PUBLIC_BASE_URL,
    S3_PROVIDER,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    STORAGE_BACKEND,
    TMP_DIR,
)

logger = logging.getLogger("parallea.storage")

LOCAL_STORAGE_DIR = DATA_DIR / "object_storage"
_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9._=-]+")
_BACKBLAZE_ENDPOINT_RE = re.compile(r"^https://s3\.([a-z0-9-]+)\.backblazeb2\.com/?$", re.I)
_RAILWAY_ENV_VARS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_DEPLOYMENT_ID",
)


@dataclass
class StoredObject:
    backend: str
    bucket: str | None
    object_key: str
    public_url: str | None = None
    presigned_url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def url(self) -> str | None:
        return self.public_url or self.presigned_url


def get_storage_backend() -> str:
    return STORAGE_BACKEND if STORAGE_BACKEND in {"local", "s3"} else "local"


def storage_enabled() -> bool:
    return get_storage_backend() == "s3"


def get_storage_provider() -> str:
    return (S3_PROVIDER or "generic").strip().lower() or "generic"


def is_production_like() -> bool:
    if any((os.getenv(name) or "").strip() for name in _RAILWAY_ENV_VARS):
        return True
    for name in ("PARALLEA_ENV", "ENVIRONMENT", "APP_ENV", "PYTHON_ENV", "NODE_ENV"):
        if (os.getenv(name) or "").strip().lower() in {"prod", "production"}:
            return True
    return False


def allow_local_fallback_for_storage_error() -> bool:
    return get_storage_backend() == "local"


def endpoint_host() -> str:
    raw = (S3_ENDPOINT_URL or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return parsed.netloc or parsed.path


def _url_host(raw_url: str) -> str:
    parsed = urlparse((raw_url or "").strip())
    return (parsed.netloc or parsed.path).lower()


def _looks_like_backblaze_s3_api_url(raw_url: str) -> bool:
    host = _url_host(raw_url)
    return bool(
        host == "s3.backblazeb2.com"
        or re.match(r"^s3\.[a-z0-9-]+\.backblazeb2\.com$", host)
        or re.match(r"^[^.]+\.s3\.[a-z0-9-]+\.backblazeb2\.com$", host)
    )


def _effective_public_base_url() -> str:
    public_base = (S3_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not public_base or not storage_enabled():
        return ""
    if get_storage_provider() == "backblaze" or _looks_like_backblaze_s3_api_url(S3_ENDPOINT_URL):
        logger.warning(
            "storage public base URL ignored for Backblaze private bucket; presigned URLs will be used provider=%s endpoint_host=%s public_base_host=%s",
            get_storage_provider(),
            endpoint_host(),
            _url_host(public_base),
        )
        return ""
    if endpoint_host() and _url_host(public_base) == endpoint_host().lower():
        logger.warning(
            "storage public base URL matches S3 endpoint host; refusing to return plain private-object URLs host=%s",
            endpoint_host(),
        )
        return ""
    return public_base


def _s3_configured() -> bool:
    return bool(S3_BUCKET_NAME and S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY)


def _backblaze_validation_errors() -> list[str]:
    if get_storage_provider() != "backblaze":
        return []
    errors: list[str] = []
    endpoint = (S3_ENDPOINT_URL or "").strip()
    match = _BACKBLAZE_ENDPOINT_RE.match(endpoint)
    if not match:
        errors.append(
            "S3_PROVIDER=backblaze requires S3_ENDPOINT_URL like "
            "https://s3.<region>.backblazeb2.com"
        )
    region = (S3_REGION or "").strip()
    if not region or region.lower() == "auto":
        errors.append("S3_PROVIDER=backblaze requires S3_REGION to be the Backblaze region, not 'auto'.")
    elif match and match.group(1).lower() != region.lower():
        errors.append(
            "S3_PROVIDER=backblaze S3_REGION should match the endpoint region "
            f"({match.group(1)})."
        )
    return errors


def _s3_validation_errors() -> list[str]:
    errors: list[str] = []
    if storage_enabled() and not _s3_configured():
        errors.append(
            "S3 storage is selected but bucket or credentials are missing. "
            "Set S3_BUCKET_NAME, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY."
        )
    errors.extend(_backblaze_validation_errors())
    return errors


def _s3_client():
    validation_errors = _s3_validation_errors()
    if validation_errors:
        raise RuntimeError(" ".join(validation_errors))
    try:
        import boto3
        from botocore.config import Config
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("S3 storage requires boto3. Add boto3 to requirements.txt.") from exc

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL or None,
        region_name=S3_REGION or None,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def safe_key_part(value: Any, default: str = "item") -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    text = text.replace("..", ".")
    text = _SAFE_PART_RE.sub("_", text).strip("._/")
    return text or default


def safe_object_key(*parts: Any) -> str:
    cleaned = [safe_key_part(part) for part in parts if str(part or "").strip()]
    if not cleaned:
        raise ValueError("object key cannot be empty")
    return "/".join(cleaned)


def content_type_for_path(path: Path, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(path.name)[0] or fallback


def _local_path(object_key: str) -> Path:
    clean_key = safe_object_key(*str(object_key).split("/"))
    return LOCAL_STORAGE_DIR / clean_key


def get_public_url(object_key: str) -> str | None:
    key = safe_object_key(*str(object_key).split("/"))
    if storage_enabled():
        public_base = _effective_public_base_url()
        if not public_base:
            return None
        return f"{public_base}/{key}"
    return f"/storage/{key}"


def get_presigned_url(object_key: str, expires_seconds: int | None = None) -> str:
    key = safe_object_key(*str(object_key).split("/"))
    if not storage_enabled():
        return get_public_url(key) or ""
    client = _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET_NAME, "Key": key},
        ExpiresIn=int(expires_seconds or S3_PRESIGNED_URL_EXPIRES_SECONDS),
    )


def url_for_object(object_key: str, expires_seconds: int | None = None) -> str:
    public_url = get_public_url(object_key)
    if public_url:
        return public_url
    return get_presigned_url(object_key, expires_seconds=expires_seconds)


def _clean_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        clean_key = safe_key_part(key, "meta").replace("_", "-").lower()[:64]
        if value is None:
            continue
        cleaned[clean_key] = str(value)[:512]
    return cleaned


def upload_file(
    local_path: str | Path,
    object_key: str,
    content_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoredObject:
    source = Path(local_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"storage upload source missing: {source}")
    key = safe_object_key(*str(object_key).split("/"))
    content_type = content_type or content_type_for_path(source)
    size_bytes = source.stat().st_size

    if not storage_enabled():
        target = _local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return StoredObject(
            backend="local",
            bucket=None,
            object_key=key,
            public_url=get_public_url(key),
            content_type=content_type,
            size_bytes=size_bytes,
        )

    client = _s3_client()
    extra_args: dict[str, Any] = {"ContentType": content_type}
    clean_metadata = _clean_metadata(metadata)
    if clean_metadata:
        extra_args["Metadata"] = clean_metadata
    client.upload_file(str(source), S3_BUCKET_NAME, key, ExtraArgs=extra_args)
    public_url = get_public_url(key)
    presigned_url = None if public_url else get_presigned_url(key)
    return StoredObject(
        backend="s3",
        bucket=S3_BUCKET_NAME,
        object_key=key,
        public_url=public_url,
        presigned_url=presigned_url,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def upload_bytes(
    data: bytes,
    object_key: str,
    content_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoredObject:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = TMP_DIR / f"storage_upload_{os.getpid()}_{int(time.time() * 1000)}"
    temp_path.write_bytes(data)
    try:
        return upload_file(temp_path, object_key, content_type=content_type, metadata=metadata)
    finally:
        temp_path.unlink(missing_ok=True)


def delete_object(object_key: str) -> bool:
    key = safe_object_key(*str(object_key).split("/"))
    try:
        if storage_enabled():
            _s3_client().delete_object(Bucket=S3_BUCKET_NAME, Key=key)
            return True
        _local_path(key).unlink(missing_ok=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage delete failed backend=%s key=%s error=%s", get_storage_backend(), key, exc)
        return False


def object_exists(object_key: str) -> bool:
    key = safe_object_key(*str(object_key).split("/"))
    try:
        if storage_enabled():
            _s3_client().head_object(Bucket=S3_BUCKET_NAME, Key=key)
            return True
        return _local_path(key).exists()
    except Exception:
        return False


def temp_dir_writable() -> bool:
    return directory_writable(TMP_DIR)


def directory_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".storage_probe_{os.getpid()}_{int(time.time() * 1000)}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def can_create_presigned_url() -> bool:
    if not storage_enabled():
        return False
    try:
        get_presigned_url("health/presign-check.txt", expires_seconds=60)
        return True
    except Exception:
        return False


def storage_status() -> dict[str, Any]:
    validation_errors = _s3_validation_errors() if storage_enabled() else []
    effective_public_base_url = _effective_public_base_url()
    return {
        "backend": get_storage_backend(),
        "provider": get_storage_provider(),
        "bucket_configured": bool(S3_BUCKET_NAME),
        "s3_configured": _s3_configured(),
        "s3_validation_errors": validation_errors,
        "endpoint_host": endpoint_host(),
        "region": S3_REGION,
        "public_base_url_configured": bool(S3_PUBLIC_BASE_URL),
        "public_base_url_effective": bool(effective_public_base_url),
        "private_presigned_mode": storage_enabled() and not bool(effective_public_base_url),
        "presigned_urls_enabled": storage_enabled() and not bool(effective_public_base_url),
        "temp_dir": str(TMP_DIR),
        "temp_dir_writable": temp_dir_writable(),
        "manim_runtime_dir": str(MANIM_RUNTIME_DIR),
        "manim_runtime_dir_writable": directory_writable(MANIM_RUNTIME_DIR),
        "can_create_presigned_url": can_create_presigned_url(),
        "local_storage_dir": str(LOCAL_STORAGE_DIR),
    }


def log_storage_status() -> dict[str, Any]:
    status = storage_status()
    logger.info(
        "storage backend=%s provider=%s bucket_configured=%s endpoint_host=%s region=%s public_base_url_configured=%s public_base_url_effective=%s presigned_urls_enabled=%s tmp_dir=%s tmp_writable=%s manim_runtime_dir=%s manim_runtime_writable=%s local_static_routes=%s",
        status["backend"],
        status["provider"],
        status["bucket_configured"],
        status["endpoint_host"] or "",
        status["region"] or "",
        status["public_base_url_configured"],
        status["public_base_url_effective"],
        status["presigned_urls_enabled"],
        status["temp_dir"],
        status["temp_dir_writable"],
        status["manim_runtime_dir"],
        status["manim_runtime_dir_writable"],
        "/rendered-scenes,/audio-response,/generated,/storage",
    )
    for error in status.get("s3_validation_errors") or []:
        logger.warning("storage configuration warning: %s", error)
    return status
