from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import (
    MANIM_ALLOW_MATHTEX,
    MANIM_DEBUG_DIR,
    MANIM_ENABLED,
    MANIM_FORCE_TEXT_ONLY,
    MANIM_PUBLIC_BASE_URL,
    MANIM_PUBLIC_OUTPUT_DIR,
    MANIM_QUALITY,
    MANIM_RENDER_TIMEOUT_SECONDS,
    MANIM_REQUIRE_LATEX,
    MANIM_RUNTIME_DIR,
    RENDERS_DIR,
    TMP_DIR,
)
from backend.services.storage_service import (
    allow_local_fallback_for_storage_error,
    safe_object_key,
    storage_enabled,
    upload_file,
)
from backend.services.generated_media_cleanup import register_generated_media


logger = logging.getLogger("parallea.manim")
MANIM_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="parallea-manim")

MANIM_DIR = RENDERS_DIR / "manim"
MANIM_SCENES_DIR = MANIM_RUNTIME_DIR / "scenes"
MANIM_WORK_DIR = MANIM_RUNTIME_DIR / "work"
MANIM_OUTPUT_DIR = MANIM_PUBLIC_OUTPUT_DIR
MANIM_LOG_DIR = MANIM_DEBUG_DIR / "logs"
MANIM_HEALTH_DIR = MANIM_RUNTIME_DIR / "health"
DEFAULT_VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
MANIM_PYTHON = Path(os.getenv("PARALLEA_MANIM_PYTHON", "")).expanduser() if os.getenv("PARALLEA_MANIM_PYTHON", "").strip() else DEFAULT_VENV_PYTHON
MANIM_RENDER_TIMEOUT_SEC = MANIM_RENDER_TIMEOUT_SECONDS
SUPPORTED_MANIM_SCENE_TYPES = {
    "concept_stack",
    "process_flow",
    "comparison_cards",
    "axes_curve",
    "vector_axes",
    "geometry_triangle",
    "number_line_steps",
    "equation_steps",
    "matrix_heatmap",
    "cycle_loop",
}
DIRECT_MANIM_RENDERER_VERSION = "openai_direct_manim_v1"
DIRECT_SCENE_CLASS_NAME = "GeneratedScene"
OPENAI_DIRECT_SCENE_CLASS_NAME = "ParalleaGeneratedScene"
DIRECT_SCENE_CLASS_NAMES = {DIRECT_SCENE_CLASS_NAME, OPENAI_DIRECT_SCENE_CLASS_NAME}
TEX_DEPENDENT_CALL_RE = re.compile(r"\b(MathTex|Tex|SingleStringMathTex)\s*\(")
TEX_DEPENDENT_NAMES = {"MathTex", "Tex", "SingleStringMathTex"}
UNSAFE_IMPORT_ROOTS = {"os", "sys", "subprocess", "socket", "requests", "urllib", "http", "httpx", "pathlib", "shutil"}
UNSAFE_CALL_NAMES = {"open", "exec", "eval", "__import__", "input", "ImageMobject", "SVGMobject"}
COLOR_REJECT_NAMES = {"Color", "ManimColor", "rgb_to_color"}
SAFE_COLOR_CONSTANTS = "WHITE, BLACK, BLUE, BLUE_E, GREEN, GREEN_E, RED, RED_E, YELLOW, ORANGE, PURPLE, GREY, GRAY"
ASSET_MOBJECT_NAMES = {"ImageMobject", "SVGMobject"}
EXTERNAL_ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".mp4", ".mov", ".wav", ".mp3", ".json", ".txt", ".csv")
MANIM_STDERR_TAIL_LINES = 120
MANIM_STDOUT_TAIL_LINES = 80
REGION_SAFE_HELPERS_CODE = '''config.frame_width = 14.222
config.frame_height = 8.0
config.pixel_width = 1280
config.pixel_height = 720

SAFE_MARGIN = 0.35
FRAME_WIDTH = 14.222
FRAME_HEIGHT = 8.0
REGION_CENTERS = {
    "title": UP * 3.25,
    "left": LEFT * 3.35 + UP * 0.05,
    "right": RIGHT * 3.25 + UP * 0.05,
    "bottom": DOWN * 3.25,
}
REGION_SIZES = {
    "title": (12.2, 0.9),
    "left": (5.6, 5.0),
    "right": (5.6, 5.0),
    "bottom": (12.0, 0.75),
}

def fit_to_region(mobject, max_width, max_height):
    if mobject.width > max_width:
        mobject.scale_to_fit_width(max_width)
    if mobject.height > max_height:
        mobject.scale_to_fit_height(max_height)
    return mobject

def keep_inside_frame(mobject):
    half_w = FRAME_WIDTH / 2 - SAFE_MARGIN
    half_h = FRAME_HEIGHT / 2 - SAFE_MARGIN
    if mobject.width > half_w * 2:
        mobject.scale_to_fit_width(half_w * 2)
    if mobject.height > half_h * 2:
        mobject.scale_to_fit_height(half_h * 2)
    dx = 0
    dy = 0
    if mobject.get_right()[0] > half_w:
        dx -= mobject.get_right()[0] - half_w
    if mobject.get_left()[0] < -half_w:
        dx += -half_w - mobject.get_left()[0]
    if mobject.get_top()[1] > half_h:
        dy -= mobject.get_top()[1] - half_h
    if mobject.get_bottom()[1] < -half_h:
        dy += -half_h - mobject.get_bottom()[1]
    if dx or dy:
        mobject.shift(RIGHT * dx + UP * dy)
    return mobject

def safe_text(text, font_size=32, max_width=5.5):
    mobject = Text(str(text or ""), font_size=min(int(font_size), 48), color=WHITE)
    fit_to_region(mobject, max_width, 1.0)
    return mobject

def bullet_list(items, max_width=5.5, font_size=28):
    rows = VGroup()
    for item in list(items or [])[:5]:
        row = safe_text("- " + str(item), font_size=font_size, max_width=max_width)
        rows.add(row)
    if len(rows):
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.18)
    fit_to_region(rows, max_width, 4.7)
    return rows

def _place_region(mobject, region_name):
    max_width, max_height = REGION_SIZES[region_name]
    fit_to_region(mobject, max_width, max_height)
    mobject.move_to(REGION_CENTERS[region_name])
    keep_inside_frame(mobject)
    return mobject

def place_title(mobject):
    return _place_region(mobject, "title")

def place_left(mobject):
    return _place_region(mobject, "left")

def place_right(mobject):
    return _place_region(mobject, "right")

def place_bottom(mobject):
    return _place_region(mobject, "bottom")

def clear_region(scene, active_regions, region_name):
    old_mobject = active_regions.get(region_name)
    if old_mobject is not None:
        scene.play(FadeOut(old_mobject), run_time=0.4)
        active_regions[region_name] = None

def replace_region(scene, active_regions, region_name, new_mobject, animation=FadeIn):
    clear_region(scene, active_regions, region_name)
    scene.play(animation(new_mobject), run_time=0.6)
    active_regions[region_name] = new_mobject
    return new_mobject

def clear_all_regions(scene, active_regions, keep_title=False):
    for region_name in list(active_regions.keys()):
        if keep_title and region_name == "title":
            continue
        clear_region(scene, active_regions, region_name)
'''

for path in [MANIM_DIR, MANIM_SCENES_DIR, MANIM_WORK_DIR, MANIM_OUTPUT_DIR, MANIM_LOG_DIR, MANIM_HEALTH_DIR]:
    path.mkdir(parents=True, exist_ok=True)


def directory_writable(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "exists": path.exists(), "writable": False, "error": ""}
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_probe_{os.getpid()}_{int(time.time() * 1000)}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        info["exists"] = True
        info["writable"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = repr(exc)
    return info


def clean_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def trim_sentence(text: Any, limit: int = 140) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def split_sentences(text: Any, limit: int = 4) -> list[str]:
    raw = clean_spaces(text)
    if not raw:
        return []
    parts = [clean_spaces(part) for part in re.split(r"(?<=[.!?])\s+", raw) if clean_spaces(part)]
    return [trim_sentence(part, 140) for part in parts[:limit]]


def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def resolve_manim_python() -> Path:
    if MANIM_PYTHON.exists():
        return MANIM_PYTHON
    return Path(sys.executable)


@lru_cache(maxsize=1)
def latex_runtime_info() -> dict[str, Any]:
    latex_path = shutil.which("latex")
    dvisvgm_path = shutil.which("dvisvgm")
    available = bool(latex_path and dvisvgm_path)
    info = {
        "latex_available": available,
        "latex_path": latex_path or "",
        "dvisvgm_available": bool(dvisvgm_path),
        "dvisvgm_path": dvisvgm_path or "",
    }
    logger.info("[manim] latex_available=%s", str(available).lower())
    logger.info("[manim] latex_path=%s", latex_path or "")
    logger.info("[manim] dvisvgm_path=%s", dvisvgm_path or "")
    return info


def has_latex_available() -> bool:
    return bool(latex_runtime_info().get("latex_available"))


def manim_mathtex_allowed() -> bool:
    if MANIM_FORCE_TEXT_ONLY:
        return False
    mode = str(MANIM_ALLOW_MATHTEX or "auto").strip().lower()
    if mode == "0":
        return False
    return has_latex_available()


def manim_allow_mathtex_effective_value() -> str:
    mode = str(MANIM_ALLOW_MATHTEX or "auto").strip().lower()
    if MANIM_FORCE_TEXT_ONLY:
        return "0 (MANIM_FORCE_TEXT_ONLY=1)"
    if mode == "0":
        return "0"
    if has_latex_available():
        return "1"
    if mode == "1":
        return "0 (MANIM_ALLOW_MATHTEX=1 but latex/dvisvgm missing)"
    return "0 (auto: latex/dvisvgm missing)"


def manim_text_only_mode() -> bool:
    """Return True when generated code must avoid MathTex/Tex/SingleStringMathTex.

    Forced when the env says so, when LaTeX is required but unavailable, when
    MathTex is disabled, or whenever the host has no LaTeX toolchain.
    """
    return not manim_mathtex_allowed()


def path_to_public_url(file_path: Path) -> str:
    """Map an absolute Manim output path to its browser-safe URL."""
    try:
        rel = Path(file_path).resolve().relative_to(MANIM_PUBLIC_OUTPUT_DIR.resolve())
        return f"{MANIM_PUBLIC_BASE_URL}/{Path(rel).as_posix()}"
    except Exception:
        pass
    try:
        rel = Path(file_path).resolve().relative_to(RENDERS_DIR.resolve())
        return f"/rendered-scenes/{Path(rel).as_posix()}"
    except Exception:
        return f"{MANIM_PUBLIC_BASE_URL}/{Path(file_path).name}"


def manim_storage_enabled() -> bool:
    return storage_enabled()


def _manim_object_key(payload: dict[str, Any] | None, segment_id: str, key: str) -> str:
    data = payload or {}
    session_id = data.get("storage_session_id") or data.get("session_id") or segment_id or "session"
    message_id = data.get("storage_message_id") or data.get("message_id") or "message"
    render_id = data.get("storage_render_id") or data.get("render_id") or key
    return safe_object_key("temp", "manim-renders", session_id, message_id, f"{render_id}.mp4")


def _manim_temp_local_path(payload: dict[str, Any] | None, segment_id: str, key: str) -> Path:
    data = payload or {}
    session_id = safe_object_key(data.get("storage_session_id") or data.get("session_id") or segment_id or "session")
    message_id = safe_object_key(data.get("storage_message_id") or data.get("message_id") or "message")
    render_id = safe_object_key(data.get("storage_render_id") or data.get("render_id") or key)
    return TMP_DIR / "manim-renders" / session_id / message_id / f"{render_id}.mp4"


def _publish_manim_video(
    final_video: Path,
    *,
    payload: dict[str, Any] | None,
    segment_id: str,
    key: str,
) -> tuple[str, dict[str, Any] | None]:
    data = payload or {}
    session_id = str(data.get("storage_session_id") or data.get("session_id") or segment_id or "")
    message_id = str(data.get("storage_message_id") or data.get("message_id") or "")
    size_bytes = final_video.stat().st_size if final_video.exists() else None
    if not storage_enabled():
        temp_path = _manim_temp_local_path(payload, segment_id, key)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        if final_video.resolve() != temp_path.resolve():
            shutil.move(str(final_video), str(temp_path))
        record = register_generated_media(
            session_id=session_id,
            message_id=message_id,
            media_type="manim_video",
            storage_backend="local",
            local_path=temp_path,
            content_type="video/mp4",
            size_bytes=size_bytes,
        )
        return str(record.get("url") or ""), {
            "backend": "local",
            "object_key": None,
            "generated_media": record,
            "local_path": str(temp_path),
        }
    object_key = _manim_object_key(payload, segment_id, key)
    try:
        stored = upload_file(
            final_video,
            object_key,
            content_type="video/mp4",
            metadata={"kind": "manim_render", "segment_id": segment_id, "render_key": key},
        )
        final_video.unlink(missing_ok=True)
        record = register_generated_media(
            session_id=session_id,
            message_id=message_id,
            media_type="manim_video",
            storage_backend="s3",
            object_key=stored.object_key,
            url=stored.url,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
        )
        stored_dict = stored.to_dict()
        stored_dict["generated_media"] = record
        return stored.url or "", stored_dict
    except Exception as exc:  # noqa: BLE001
        logger.exception("manim storage upload failed key=%s object_key=%s: %s", key, object_key, exc)
        if allow_local_fallback_for_storage_error():
            cache_bust = int(final_video.stat().st_mtime) if final_video.exists() else 0
            media_url_no_bust = path_to_public_url(final_video)
            media_url = f"{media_url_no_bust}?v={cache_bust}" if cache_bust else media_url_no_bust
            return media_url, {"backend": "local", "object_key": None, "error": str(exc)}
        raise


def command_to_string(command: list[str]) -> str:
    try:
        return subprocess.list2cmdline([str(item) for item in command])
    except Exception:
        return " ".join(str(item) for item in command)


def write_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


def validate_scene_source(scene_source: str, scene_file: Path) -> None:
    compile(scene_source, str(scene_file), "exec")


def strip_code_fences(code: str) -> str:
    text = str(code or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:python)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def contains_tex_dependent_manim(code: str) -> bool:
    return bool(TEX_DEPENDENT_CALL_RE.search(str(code or "")))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _import_root(name: str | None) -> str:
    return (name or "").split(".", 1)[0]


def _looks_like_external_asset(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if "://" in text:
        return True
    normalized = text.replace("\\", "/")
    return any(normalized.endswith(ext) or f"{ext}?" in normalized for ext in EXTERNAL_ASSET_EXTENSIONS)


def _risky_layout_error(text: str) -> str | None:
    if not re.search(r"active_regions\s*=\s*\{", text) or "replace_region(self" not in text:
        return "layout risk: generated scene must use active_regions and replace_region for region-safe replacement"
    for match in re.finditer(r"\.shift\s*\(\s*(UP|DOWN|LEFT|RIGHT)\s*\*\s*([0-9]+(?:\.[0-9]+)?)", text):
        direction = match.group(1)
        amount = float(match.group(2))
        limit = 3.4 if direction in {"UP", "DOWN"} else 6.0
        if amount >= limit:
            return f"layout risk: extreme {direction} shift {amount} can crop the frame"
    for match in re.finditer(r"\.shift\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s*\*\s*(UP|DOWN|LEFT|RIGHT)", text):
        amount = float(match.group(1))
        direction = match.group(2)
        limit = 3.4 if direction in {"UP", "DOWN"} else 6.0
        if amount >= limit:
            return f"layout risk: extreme {direction} shift {amount} can crop the frame"
    for match in re.finditer(r"\.to_edge\s*\(([^)]*)\)", text):
        args = match.group(1)
        if "buff" not in args:
            return "layout risk: to_edge must use buff >= 0.3 or region placement helpers"
        buff_match = re.search(r"buff\s*=\s*([0-9]+(?:\.[0-9]+)?)", args)
        if buff_match and float(buff_match.group(1)) < 0.3:
            return "layout risk: to_edge buff must be at least 0.3"
    for match in re.finditer(r"font_size\s*=\s*([0-9]+)", text):
        if int(match.group(1)) > 52:
            return f"layout risk: font_size {match.group(1)} is too large for safe 16:9 layout"
    text_call_count = len(re.findall(r"\b(?:Text|MarkupText|MathTex|Tex)\s*\(", text))
    if text_call_count and not any(token in text for token in ("safe_text(", "fit_to_region(", "scale_to_fit_width")):
        return "layout risk: Text/MathTex objects must be created with safe_text or fitted to a region"
    if text_call_count > 12 and "bullet_list(" not in text:
        return "layout risk: too many text objects without grouping into bullet_list or region groups"
    return None


def latex_render_failure(stdout: str, stderr: str, scene_source: str) -> bool:
    haystack = f"{stdout}\n{stderr}\n{scene_source}".lower()
    if "tex_file_writing.py" in haystack or "mathtex" in haystack or "singlestringmathtex" in haystack:
        return any(token in haystack for token in ("filenotfounderror", "winerror 2", "latex", "dvisvgm", "tex"))
    if contains_tex_dependent_manim(scene_source) and not has_latex_available():
        return True
    return False


def normalize_direct_scene_class_name(value: Any = "") -> str:
    name = clean_spaces(value)
    return name if name in DIRECT_SCENE_CLASS_NAMES else DIRECT_SCENE_CLASS_NAME


def direct_manim_validation_error(code: str, *, scene_class_name: str = DIRECT_SCENE_CLASS_NAME) -> str | None:
    text = strip_code_fences(code)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return f"python syntax error: {exc}"

    expected_scene_class = normalize_direct_scene_class_name(scene_class_name)
    has_manim_star_import = False
    class_names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    if class_names != [expected_scene_class]:
        return f"generated code must define exactly one scene class `{expected_scene_class}`; found {class_names}"

    lowered = text.lower()
    banned_color_tokens = ["manimcolor(", "hsl=", "rgb_to_color", "from colour", "import colour", "from manim.utils.color"]
    for token in banned_color_tokens:
        if token in lowered:
            return f"unsupported color construct: {token}"
    for asset_name in ASSET_MOBJECT_NAMES:
        if re.search(rf"\b{asset_name}\s*\(", text):
            return f"external assets are not allowed: {asset_name}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _import_root(alias.name)
                if root in UNSAFE_IMPORT_ROOTS:
                    return f"unsafe import: {alias.name}"
                return "generated code must use `from manim import *` as its only import"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = _import_root(module)
            if module == "manim" and len(node.names) == 1 and node.names[0].name == "*":
                has_manim_star_import = True
                continue
            if root in UNSAFE_IMPORT_ROOTS or root == "colour" or module.startswith("manim.utils.color"):
                return f"unsafe import: from {module}"
            return "generated code must use `from manim import *` as its only import"
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in UNSAFE_CALL_NAMES:
                return f"unsafe call: {name}("
            if name in COLOR_REJECT_NAMES:
                return f"unsupported color construct: {name}("
            if name in TEX_DEPENDENT_NAMES and manim_text_only_mode():
                logger.warning("[manim] generated code contains MathTex/Tex while text-only mode is active")
                return "MathTex/Tex requires LaTeX and is disabled or unavailable; use Text(...) formulas"
            for keyword in node.keywords:
                if keyword.arg == "hsl":
                    return "unsupported color construct: hsl="
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in UNSAFE_IMPORT_ROOTS:
                return f"unsafe reference: {node.value.id}.{node.attr}"
            if node.attr in COLOR_REJECT_NAMES:
                return f"unsupported color construct: {node.attr}"
            if node.attr in ASSET_MOBJECT_NAMES:
                return f"external assets are not allowed: {node.attr}"
        elif isinstance(node, ast.Name):
            if node.id in UNSAFE_IMPORT_ROOTS:
                return f"unsafe reference: {node.id}"
            if node.id in ASSET_MOBJECT_NAMES:
                return f"external assets are not allowed: {node.id}"
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str) and _looks_like_external_asset(node.value):
                return "external file paths, URLs, and assets are not allowed in generated Manim code"

    if not has_manim_star_import:
        return "missing `from manim import *`"
    layout_error = _risky_layout_error(text)
    if layout_error:
        return layout_error
    return None


def fallback_direct_manim_code(
    payload: dict[str, Any] | None = None,
    reason: str = "",
    *,
    scene_class_name: str = DIRECT_SCENE_CLASS_NAME,
) -> str:
    payload = payload or {}
    scene_class_name = normalize_direct_scene_class_name(scene_class_name or payload.get("scene_class_name"))
    title = json.dumps(trim_sentence(payload.get("title") or "Clarification", 44))
    subtitle_text = payload.get("subtitle") or reason or "Break the idea into small connected steps."
    bullets = split_sentences(subtitle_text, limit=4) or [
        "Break the idea into small steps.",
        "Watch how each step connects.",
        "Use the result to answer the doubt.",
    ]
    bullets = [json.dumps(trim_sentence(item, 76)) for item in bullets[:4]]
    bullets_literal = "[" + ", ".join(bullets) + "]"
    return f'''from manim import *

{REGION_SAFE_HELPERS_CODE}

class {scene_class_name}(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        active_regions = {{"title": None, "left": None, "right": None, "bottom": None}}
        title = safe_text({title}, font_size=36, max_width=12.0)
        place_title(title)
        replace_region(self, active_regions, "title", title)

        bullets = {bullets_literal}
        rows = bullet_list(bullets, max_width=5.2, font_size=25)
        place_left(rows)
        replace_region(self, active_regions, "left", rows)

        left = Circle(radius=0.36, color=GREEN)
        middle = Rectangle(width=1.35, height=0.66, color=YELLOW)
        right = Circle(radius=0.36, color=ORANGE)
        flow = VGroup(left, middle, right).arrange(RIGHT, buff=0.7)
        arrows = VGroup(
            Arrow(left.get_right(), middle.get_left(), buff=0.12, color=BLUE),
            Arrow(middle.get_right(), right.get_left(), buff=0.12, color=BLUE),
        )
        diagram = VGroup(flow, arrows)
        place_right(diagram)
        replace_region(self, active_regions, "right", diagram)
        follow = safe_text("Does that make sense now?", font_size=24, max_width=11.5)
        place_bottom(follow)
        replace_region(self, active_regions, "bottom", follow)
        self.wait(2)
'''


def prepare_direct_manim_code(
    code: str,
    payload: dict[str, Any] | None = None,
    *,
    scene_class_name: str = DIRECT_SCENE_CLASS_NAME,
) -> tuple[str, dict[str, Any]]:
    scene_class_name = normalize_direct_scene_class_name(scene_class_name or (payload or {}).get("scene_class_name"))
    text = strip_code_fences(code)
    found_classes = re.findall(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, flags=re.M)
    if len(found_classes) == 1 and found_classes[0] in DIRECT_SCENE_CLASS_NAMES and found_classes[0] != scene_class_name:
        text = re.sub(
            rf"class\s+{re.escape(found_classes[0])}\s*\(",
            f"class {scene_class_name}(",
            text,
            count=1,
        )
    validation_error = direct_manim_validation_error(text, scene_class_name=scene_class_name)
    if validation_error:
        logger.warning("manim direct code validation failed error=%s; using fallback scene", validation_error)
        return fallback_direct_manim_code(payload, validation_error, scene_class_name=scene_class_name), {
            "valid": False,
            "error": validation_error,
            "fallback_used": True,
        }
    try:
        compile(text, "<generated_manim>", "exec")
    except Exception as exc:
        logger.warning("manim direct code compile failed error=%s; using fallback scene", exc)
        return fallback_direct_manim_code(payload, str(exc), scene_class_name=scene_class_name), {
            "valid": False,
            "error": str(exc),
            "fallback_used": True,
        }
    return text, {"valid": True, "error": None, "fallback_used": False}


def risky_python_version(version_text: str) -> bool:
    match = re.search(r"(\d+)\.(\d+)", version_text or "")
    if not match:
        return False
    major = int(match.group(1))
    minor = int(match.group(2))
    return major > 3 or (major == 3 and minor >= 13)


@lru_cache(maxsize=1)
def manim_runtime_info() -> dict[str, Any]:
    python_exec = resolve_manim_python()
    latex_info = latex_runtime_info()
    ffmpeg_path = shutil.which("ffmpeg")
    runtime_dirs = {
        "runtime": directory_writable(MANIM_RUNTIME_DIR),
        "scenes": directory_writable(MANIM_SCENES_DIR),
        "work": directory_writable(MANIM_WORK_DIR),
        "output": directory_writable(MANIM_OUTPUT_DIR),
        "debug_logs": directory_writable(MANIM_LOG_DIR),
        "health": directory_writable(MANIM_HEALTH_DIR),
    }
    info = {
        "python_executable": str(python_exec),
        "python_version": "",
        "manim_importable": False,
        "manim_version": None,
        "manim_import_error": None,
        "risky_python": False,
        "ffmpeg_available": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path or "",
        "latex_available": bool(latex_info.get("latex_available")),
        "latex_path": latex_info.get("latex_path") or "",
        "dvisvgm_available": bool(latex_info.get("dvisvgm_available")),
        "dvisvgm_path": latex_info.get("dvisvgm_path") or "",
        "runtime_directories_writable": all(item.get("writable") for item in runtime_dirs.values()),
        "runtime_directories": runtime_dirs,
        "manim_allow_mathtex": MANIM_ALLOW_MATHTEX,
        "manim_allow_mathtex_effective": manim_allow_mathtex_effective_value(),
        "manim_require_latex": MANIM_REQUIRE_LATEX,
        "manim_force_text_only": MANIM_FORCE_TEXT_ONLY,
        "latex_required_missing": bool(MANIM_REQUIRE_LATEX and not latex_info.get("latex_available")),
    }
    probe = [
        str(python_exec),
        "-c",
        "\n".join(
            [
                "import json",
                "import sys",
                "info = {'python_executable': sys.executable, 'python_version': sys.version.split()[0]}",
                "try:",
                "    import manim",
                "    info['manim_importable'] = True",
                "    info['manim_version'] = getattr(manim, '__version__', None)",
                "except Exception as exc:",
                "    info['manim_importable'] = False",
                "    info['manim_import_error'] = repr(exc)",
                "print(json.dumps(info))",
            ]
        ),
    ]
    try:
        result = subprocess.run(probe, capture_output=True, text=True, timeout=20)
        raw = (result.stdout or "").strip()
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                info.update(parsed)
        if result.returncode != 0 and not info.get("manim_import_error"):
            info["manim_import_error"] = (result.stderr or result.stdout or "").strip()
    except Exception as exc:
        info["manim_import_error"] = repr(exc)
    info["risky_python"] = risky_python_version(str(info.get("python_version") or ""))
    return info


def log_manim_runtime_status() -> dict[str, Any]:
    info = manim_runtime_info()
    logger.info(
        "manim-runtime python=%s version=%s manim_importable=%s manim_version=%s ffmpeg_available=%s latex_available=%s dvisvgm_available=%s runtime_dirs_writable=%s runtime_dir=%s mathtex_effective=%s",
        info.get("python_executable"),
        info.get("python_version"),
        info.get("manim_importable"),
        info.get("manim_version"),
        info.get("ffmpeg_available"),
        info.get("latex_available"),
        info.get("dvisvgm_available"),
        info.get("runtime_directories_writable"),
        MANIM_RUNTIME_DIR,
        info.get("manim_allow_mathtex_effective"),
    )
    logger.info(
        "manim-runtime dirs runtime=%s scenes=%s work=%s debug_logs=%s health=%s output=%s writable=%s",
        MANIM_RUNTIME_DIR,
        MANIM_SCENES_DIR,
        MANIM_WORK_DIR,
        MANIM_LOG_DIR,
        MANIM_HEALTH_DIR,
        MANIM_OUTPUT_DIR,
        info.get("runtime_directories_writable"),
    )
    if not info.get("ffmpeg_available"):
        logger.error("manim-runtime ffmpeg was not found in PATH")
    if not info.get("runtime_directories_writable"):
        logger.error("manim-runtime directories are not all writable: %s", info.get("runtime_directories"))
    if info.get("latex_required_missing"):
        logger.error("manim-runtime MANIM_REQUIRE_LATEX=1 but latex/dvisvgm were not found in PATH")
    if info.get("risky_python"):
        logger.warning(
            "manim-runtime using Python %s. Manim is often more reliable on Python 3.11 or 3.12 than very new interpreters.",
            info.get("python_version"),
        )
    if not info.get("manim_importable"):
        logger.warning("manim-runtime import probe failed: %s", info.get("manim_import_error"))
    return info


def get_manim_render_executor() -> ThreadPoolExecutor:
    return MANIM_RENDER_EXECUTOR


def payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def scene_title(segment: dict[str, Any], lesson_plan: dict[str, Any]) -> str:
    return trim_sentence(segment.get("label") or lesson_plan.get("topic") or "Lesson frame", 48)


def text_blob(question: str, lesson_plan: dict[str, Any], segment: dict[str, Any]) -> str:
    pieces = [
        question,
        segment.get("label", ""),
        segment.get("frame_goal", ""),
        segment.get("speech_text", ""),
        " ".join(lesson_plan.get("key_ideas") or []),
        " ".join(
            item.get("formula", "")
            for item in (lesson_plan.get("key_formulas") or [])
            if isinstance(item, dict)
        ),
        " ".join(step.get("key_idea", "") for step in (lesson_plan.get("teaching_steps") or []) if isinstance(step, dict)),
        " ".join(step.get("formula", "") for step in (lesson_plan.get("teaching_steps") or []) if isinstance(step, dict)),
    ]
    return clean_spaces(" ".join(str(item or "") for item in pieces)).lower()


def count_terms(text: str, terms: list[str]) -> int:
    return sum(1 for term in terms if term in text)


def step_for_segment(lesson_plan: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    step_id = clean_spaces(segment.get("step_id"))
    if not step_id:
        return {}
    for step in lesson_plan.get("teaching_steps") or []:
        if isinstance(step, dict) and clean_spaces(step.get("step_id")) == step_id:
            return step
    return {}


def formula_details(lesson_plan: dict[str, Any], segment: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    step = step_for_segment(lesson_plan, segment)
    formula = clean_spaces(step.get("formula"))
    terms = []
    for item in step.get("formula_terms") or []:
        if not isinstance(item, dict):
            continue
        term = trim_sentence(item.get("term"), 32)
        meaning = trim_sentence(item.get("meaning"), 72)
        if term and meaning:
            terms.append({"term": term, "meaning": meaning})
    if formula:
        return trim_sentence(formula, 80), terms[:4]
    for item in lesson_plan.get("key_formulas") or []:
        if not isinstance(item, dict):
            continue
        candidate = trim_sentence(item.get("formula"), 80)
        if not candidate:
            continue
        meaning = trim_sentence(item.get("meaning"), 72)
        when_to_use = trim_sentence(item.get("when_to_use"), 72)
        fallback_terms = []
        if meaning:
            fallback_terms.append({"term": candidate, "meaning": meaning})
        elif when_to_use:
            fallback_terms.append({"term": candidate, "meaning": when_to_use})
        return candidate, (terms or fallback_terms)[:4]
    return "", terms[:4]


def looks_like_projectile_motion(text: str) -> bool:
    return any(
        term in text
        for term in [
            "projectile",
            "trajectory",
            "parabola",
            "parabolic",
            "launch angle",
            "launch speed",
            "ballistic",
            "cannon",
            "gravity",
            "height vs time",
            "range",
        ]
    )


def manim_relevance_score(question: str, lesson_plan: dict[str, Any], segment: dict[str, Any]) -> int:
    text = text_blob(question, lesson_plan, segment)
    score = 0
    score += count_terms(text, ["graph", "plot", "curve", "function", "slope", "coordinate", "derivative", "integral", "limit"]) * 3
    score += count_terms(text, ["trajectory", "projectile", "parabola", "parabolic", "launch", "gravity", "height", "time"]) * 4
    score += count_terms(text, ["vector", "force", "magnitude", "direction", "component", "projection", "velocity", "acceleration"]) * 3
    score += count_terms(text, ["triangle", "angle", "geometry", "vertex", "hypotenuse", "polygon", "circle", "radius"]) * 3
    score += count_terms(text, ["matrix", "tensor", "array", "grid", "table", "heatmap"]) * 3
    score += count_terms(text, ["equation", "algebra", "solve", "expression", "formula", "identity", "balance"]) * 3
    score += count_terms(text, ["number line", "interval", "range", "probability", "ratio", "fraction", "sequence", "series"]) * 2
    score += count_terms(text, ["cycle", "loop", "feedback", "orbit", "periodic", "oscillation", "rotation"]) * 2
    score += count_terms(text, ["process", "pipeline", "flow", "steps", "mechanism", "how"]) * 1
    if any(ch.isdigit() for ch in text):
        score += 1
    return score


def heuristic_manim_payload(question: str, lesson_plan: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    text = text_blob(question, lesson_plan, segment)
    duration = round(clamp(safe_float((segment.get("timing_hint") or {}).get("target_duration_sec"), 6.0), 4.0, 10.0), 1)
    title = scene_title(segment, lesson_plan)
    subtitle = trim_sentence(segment.get("frame_goal") or segment.get("speech_text") or question, 88)
    lesson_steps = [trim_sentence(step.get("label") or step.get("key_idea"), 34) for step in (lesson_plan.get("teaching_steps") or []) if clean_spaces(step.get("label") or step.get("key_idea"))]
    key_ideas = [trim_sentence(item, 44) for item in (lesson_plan.get("key_ideas") or []) if clean_spaces(item)]
    sentence_bits = split_sentences(segment.get("speech_text"), limit=4) or split_sentences(lesson_plan.get("answer_summary"), limit=4)
    formula, term_labels = formula_details(lesson_plan, segment)

    if looks_like_projectile_motion(text):
        return {
            "scene_type": "axes_curve",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "x_label": "time",
            "y_label": "height",
            "x_range": [0, 6, 1],
            "y_range": [0, 8, 1],
            "point_pairs": [[0, 0], [1, 2.4], [2, 4.2], [3, 4.8], [4, 4.0], [5, 2.2], [6, 0]],
            "curve_kind": "parabola",
            "curve_formula": formula or r"y = v_{0y} t - \frac{1}{2} g t^2",
            "highlight_points": [
                {"x": 0, "y": 0, "label": "launch", "color": "#e86c2f"},
                {"x": 3, "y": 4.8, "label": "peak", "color": "#f2b84b"},
                {"x": 6, "y": 0, "label": "landing", "color": "#7dd3fc"},
            ],
            "term_labels": term_labels
            or [
                {"term": "t", "meaning": "time after launch"},
                {"term": "g", "meaning": "downward acceleration from gravity"},
            ],
            "graph_label": trim_sentence(segment.get("label") or "Projectile path", 28),
            "relationship": trim_sentence(
                segment.get("frame_goal") or "Horizontal progress continues while gravity bends the path downward into a parabola.",
                72,
            ),
        }

    if any(term in text for term in ["equation", "algebra", "solve", "expression", "formula", "identity", "balance"]):
        steps = sentence_bits[:3] or ["Start equation", "Transform it", "Final form"]
        start_equation = trim_sentence(formula or steps[0], 48)
        end_equation = trim_sentence(steps[-1] if len(steps) > 1 else formula or steps[0], 48)
        return {
            "scene_type": "equation_steps",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "start_equation": start_equation,
            "end_equation": end_equation,
            "equation_lines": [start_equation, end_equation] if end_equation and end_equation != start_equation else [start_equation],
            "transform_label": trim_sentence(segment.get("label") or "transform", 18),
            "focus_points": key_ideas[:3] or steps[:3],
            "term_labels": term_labels[:4],
        }
    if any(term in text for term in ["number line", "interval", "range", "probability", "ratio", "fraction", "sequence", "series"]):
        return {
            "scene_type": "number_line_steps",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "min_value": -2,
            "max_value": 6,
            "step": 1,
            "points": [
                {"value": 0, "label": "start", "color": "#e86c2f"},
                {"value": 2, "label": trim_sentence(segment.get("label") or "move", 18), "color": "#7dd3fc"},
                {"value": 4, "label": "result", "color": "#f2b84b"},
            ],
            "intervals": [{"start": 0, "end": 4, "label": trim_sentence(segment.get("frame_goal") or "visible range", 24), "color": "#7dd3fc"}],
            "emphasis": trim_sentence(segment.get("frame_goal") or subtitle, 40),
        }
    if any(term in text for term in ["matrix", "tensor", "array", "grid", "table", "heatmap"]):
        return {
            "scene_type": "matrix_heatmap",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "values": [[0.1, 0.45, 0.8], [0.25, 0.65, 0.35], [0.9, 0.55, 0.2]],
            "row_labels": ["r1", "r2", "r3"],
            "col_labels": ["c1", "c2", "c3"],
            "emphasis": trim_sentence(segment.get("frame_goal") or "Notice which cells become strongest.", 44),
        }
    if any(term in text for term in ["cycle", "loop", "feedback", "orbit", "periodic", "oscillation", "rotation"]):
        nodes = lesson_steps[:4] or key_ideas[:4] or sentence_bits[:4] or ["state 1", "state 2", "state 3"]
        return {
            "scene_type": "cycle_loop",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "nodes": nodes[:4],
            "center_label": trim_sentence(segment.get("label") or "Cycle", 18),
            "relationship": trim_sentence(segment.get("frame_goal") or "Follow the repeating change.", 44),
        }
    if any(term in text for term in ["vector", "force", "direction", "magnitude", "axis", "axes"]):
        return {
            "scene_type": "vector_axes",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "x_label": "x",
            "y_label": "y",
            "vectors": [
                {"x": 2.5, "y": 1.6, "label": trim_sentence(segment.get("label") or "main vector", 22), "color": "#e86c2f"},
                {"x": 1.3, "y": 2.6, "label": "support", "color": "#7dd3fc"},
            ],
        }
    if any(term in text for term in ["graph", "plot", "curve", "function", "slope", "trend", "coordinate"]):
        return {
            "scene_type": "axes_curve",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "x_label": "input",
            "y_label": "output",
            "x_range": [-4, 4, 1],
            "y_range": [-2, 5, 1],
            "point_pairs": [[-3, -1], [-1, 0.2], [0, 1], [2, 2.4], [3.3, 3.1]],
            "curve_kind": "smooth",
            "curve_formula": formula,
            "highlight_points": [
                {"x": 0, "y": 1, "label": "reference", "color": "#f2b84b"},
                {"x": 2, "y": 2.4, "label": "trend", "color": "#7dd3fc"},
            ],
            "term_labels": term_labels[:4],
            "graph_label": trim_sentence(segment.get("frame_goal") or "relationship", 24),
            "relationship": trim_sentence(segment.get("frame_goal") or "Track how the output changes as the input moves.", 64),
        }
    if any(term in text for term in ["triangle", "angle", "geometry", "vertex", "side", "hypotenuse"]):
        return {
            "scene_type": "geometry_triangle",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "labels": ["A", "B", "C"],
            "emphasis": trim_sentence(segment.get("frame_goal") or "focus on the highlighted angle", 40),
        }
    if any(term in text for term in ["compare", "difference", "versus", "vs"]):
        left_points = key_ideas[:2] or split_sentences(segment.get("speech_text"), limit=2) or ["left idea"]
        right_points = split_sentences(lesson_plan.get("answer_summary"), limit=2) or ["right idea"]
        return {
            "scene_type": "comparison_cards",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "left_title": "Side A",
            "right_title": "Side B",
            "left_points": left_points[:3],
            "right_points": right_points[:3],
            "relationship": "Compare what stays fixed and what changes.",
        }
    if any(term in text for term in ["process", "pipeline", "sequence", "steps", "how", "flow"]):
        steps = lesson_steps[:4] or sentence_bits[:4] or ["start", "change", "result"]
        return {
            "scene_type": "process_flow",
            "title": title,
            "subtitle": subtitle,
            "duration_sec": duration,
            "steps": steps[:4],
        }
    cards = key_ideas[:3] or sentence_bits[:3] or [subtitle]
    return {
        "scene_type": "concept_stack",
        "title": title,
        "subtitle": subtitle,
        "duration_sec": duration,
        "cards": cards[:3],
    }


def normalize_term_labels(raw_items: Any, fallback_items: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    labels = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        term = trim_sentence(item.get("term"), 32)
        meaning = trim_sentence(item.get("meaning"), 72)
        if not term or not meaning:
            continue
        labels.append({"term": term, "meaning": meaning})
    return labels[:4] or list(fallback_items or [])[:4]


def normalize_highlight_points(raw_items: Any, fallback_items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    points = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        points.append(
            {
                "x": safe_float(item.get("x"), 0.0),
                "y": safe_float(item.get("y"), 0.0),
                "label": trim_sentence(item.get("label") or "point", 24),
                "color": clean_spaces(item.get("color")) or "#f2b84b",
            }
        )
    return points[:4] or list(fallback_items or [])[:4]


def normalize_axis_range(raw_value: Any, fallback: list[float]) -> list[float]:
    values = []
    for item in raw_value or []:
        values.append(safe_float(item, fallback[min(len(values), len(fallback) - 1)]))
        if len(values) == 3:
            break
    if len(values) != 3:
        values = list(fallback)
    start, end, step = values
    if start == end:
        end = start + 1
    step = step if step not in {0, 0.0} else fallback[2]
    if step == 0:
        step = 1
    if start > end:
        start, end = end, start
    return [start, end, step]


def normalize_manim_payload(raw_payload: Any, question: str, lesson_plan: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    fallback = heuristic_manim_payload(question, lesson_plan, segment)
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    scene_type = clean_spaces(payload.get("scene_type")).lower()
    if scene_type not in SUPPORTED_MANIM_SCENE_TYPES:
        return fallback
    fallback_scene_type = clean_spaces(fallback.get("scene_type")).lower()
    if (
        scene_type == "concept_stack"
        and fallback_scene_type
        and fallback_scene_type != "concept_stack"
        and manim_relevance_score(question, lesson_plan, segment) > 0
    ):
        logger.info(
            "manim-payload upgraded weak concept_stack to %s for segment=%s",
            fallback_scene_type,
            clean_spaces(segment.get("segment_id")) or "?",
        )
        return fallback
    normalized = {
        "scene_type": scene_type,
        "title": trim_sentence(payload.get("title") or fallback["title"], 48),
        "subtitle": trim_sentence(payload.get("subtitle") or fallback["subtitle"], 88),
        "duration_sec": round(clamp(safe_float(payload.get("duration_sec"), fallback["duration_sec"]), 4.0, 10.0), 1),
    }
    if scene_type == "process_flow":
        steps = [trim_sentence(item, 34) for item in (payload.get("steps") or []) if clean_spaces(item)]
        normalized["steps"] = steps[:4] or fallback["steps"]
    elif scene_type == "equation_steps":
        normalized["start_equation"] = trim_sentence(payload.get("start_equation") or fallback["start_equation"], 48)
        normalized["end_equation"] = trim_sentence(payload.get("end_equation") or fallback["end_equation"], 48)
        normalized["equation_lines"] = [
            trim_sentence(item, 48)
            for item in (payload.get("equation_lines") or [])
            if clean_spaces(item)
        ][:3] or fallback.get("equation_lines") or [normalized["start_equation"]]
        normalized["transform_label"] = trim_sentence(payload.get("transform_label") or fallback["transform_label"], 18)
        normalized["focus_points"] = [trim_sentence(item, 34) for item in (payload.get("focus_points") or []) if clean_spaces(item)][:3] or fallback["focus_points"]
        normalized["term_labels"] = normalize_term_labels(payload.get("term_labels"), fallback.get("term_labels"))
    elif scene_type == "number_line_steps":
        points = []
        for item in payload.get("points") or []:
            if not isinstance(item, dict):
                continue
            points.append(
                {
                    "value": safe_float(item.get("value"), 0.0),
                    "label": trim_sentence(item.get("label") or "point", 18),
                    "color": clean_spaces(item.get("color")) or "#e86c2f",
                }
            )
        intervals = []
        for item in payload.get("intervals") or []:
            if not isinstance(item, dict):
                continue
            start = safe_float(item.get("start"), 0.0)
            end = safe_float(item.get("end"), start)
            intervals.append(
                {
                    "start": min(start, end),
                    "end": max(start, end),
                    "label": trim_sentence(item.get("label") or "interval", 24),
                    "color": clean_spaces(item.get("color")) or "#7dd3fc",
                }
            )
        normalized["min_value"] = safe_float(payload.get("min_value"), fallback["min_value"])
        normalized["max_value"] = safe_float(payload.get("max_value"), fallback["max_value"])
        normalized["step"] = max(0.5, safe_float(payload.get("step"), fallback["step"]))
        normalized["points"] = points[:4] or fallback["points"]
        normalized["intervals"] = intervals[:2] or fallback["intervals"]
        normalized["emphasis"] = trim_sentence(payload.get("emphasis") or fallback["emphasis"], 44)
    elif scene_type == "comparison_cards":
        normalized["left_title"] = trim_sentence(payload.get("left_title") or fallback["left_title"], 24)
        normalized["right_title"] = trim_sentence(payload.get("right_title") or fallback["right_title"], 24)
        normalized["left_points"] = [trim_sentence(item, 34) for item in (payload.get("left_points") or []) if clean_spaces(item)][:3] or fallback["left_points"]
        normalized["right_points"] = [trim_sentence(item, 34) for item in (payload.get("right_points") or []) if clean_spaces(item)][:3] or fallback["right_points"]
        normalized["relationship"] = trim_sentence(payload.get("relationship") or fallback["relationship"], 56)
    elif scene_type == "matrix_heatmap":
        rows = []
        for row in payload.get("values") or []:
            if not isinstance(row, list):
                continue
            cleaned = [clamp(safe_float(value, 0.0), 0.0, 1.0) for value in row[:5]]
            if cleaned:
                rows.append(cleaned)
        normalized["values"] = rows[:5] or fallback["values"]
        row_count = len(normalized["values"])
        col_count = max(len(row) for row in normalized["values"])
        normalized["row_labels"] = [trim_sentence(item, 10) for item in (payload.get("row_labels") or []) if clean_spaces(item)][:row_count] or fallback["row_labels"][:row_count]
        normalized["col_labels"] = [trim_sentence(item, 10) for item in (payload.get("col_labels") or []) if clean_spaces(item)][:col_count] or fallback["col_labels"][:col_count]
        normalized["emphasis"] = trim_sentence(payload.get("emphasis") or fallback["emphasis"], 44)
    elif scene_type == "axes_curve":
        point_pairs = []
        for item in payload.get("point_pairs") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                point_pairs.append([safe_float(item[0], 0.0), safe_float(item[1], 0.0)])
        normalized["x_label"] = trim_sentence(payload.get("x_label") or fallback["x_label"], 18)
        normalized["y_label"] = trim_sentence(payload.get("y_label") or fallback["y_label"], 18)
        normalized["point_pairs"] = point_pairs[:8] or fallback["point_pairs"]
        normalized["x_range"] = normalize_axis_range(payload.get("x_range"), fallback.get("x_range") or [-4, 4, 1])
        normalized["y_range"] = normalize_axis_range(payload.get("y_range"), fallback.get("y_range") or [-2, 5, 1])
        curve_kind = clean_spaces(payload.get("curve_kind")).lower()
        if curve_kind not in {"smooth", "line", "parabola", "trajectory"}:
            curve_kind = clean_spaces(fallback.get("curve_kind")).lower() or "smooth"
        normalized["curve_kind"] = curve_kind
        normalized["curve_formula"] = trim_sentence(payload.get("curve_formula") or fallback.get("curve_formula"), 80)
        normalized["highlight_points"] = normalize_highlight_points(payload.get("highlight_points"), fallback.get("highlight_points"))
        normalized["term_labels"] = normalize_term_labels(payload.get("term_labels"), fallback.get("term_labels"))
        normalized["graph_label"] = trim_sentence(payload.get("graph_label") or fallback["graph_label"], 28)
        normalized["relationship"] = trim_sentence(payload.get("relationship") or fallback.get("relationship"), 72)
    elif scene_type == "vector_axes":
        vectors = []
        for item in payload.get("vectors") or []:
            if not isinstance(item, dict):
                continue
            vectors.append(
                {
                    "x": safe_float(item.get("x"), 1.0),
                    "y": safe_float(item.get("y"), 1.0),
                    "label": trim_sentence(item.get("label") or "vector", 24),
                    "color": clean_spaces(item.get("color")) or "#e86c2f",
                }
            )
        normalized["x_label"] = trim_sentence(payload.get("x_label") or fallback["x_label"], 18)
        normalized["y_label"] = trim_sentence(payload.get("y_label") or fallback["y_label"], 18)
        normalized["vectors"] = vectors[:3] or fallback["vectors"]
    elif scene_type == "geometry_triangle":
        normalized["labels"] = [trim_sentence(item, 6) for item in (payload.get("labels") or []) if clean_spaces(item)][:3] or fallback["labels"]
        normalized["emphasis"] = trim_sentence(payload.get("emphasis") or fallback["emphasis"], 48)
    elif scene_type == "cycle_loop":
        normalized["nodes"] = [trim_sentence(item, 24) for item in (payload.get("nodes") or []) if clean_spaces(item)][:5] or fallback["nodes"]
        normalized["center_label"] = trim_sentence(payload.get("center_label") or fallback["center_label"], 18)
        normalized["relationship"] = trim_sentence(payload.get("relationship") or fallback["relationship"], 44)
    else:
        normalized["cards"] = [trim_sentence(item, 40) for item in (payload.get("cards") or []) if clean_spaces(item)][:3] or fallback["cards"]
    return normalized


def build_scene_source(scene_name: str, payload: dict[str, Any]) -> str:
    payload_literal = json.dumps(payload, ensure_ascii=False)
    latex_available_literal = "True" if manim_mathtex_allowed() else "False"
    return f'''from manim import *
import json
import numpy as np

config.frame_width = 14.222
config.frame_height = 8.0
config.pixel_width = 1280
config.pixel_height = 720

PAYLOAD = json.loads({payload_literal!r})
LATEX_AVAILABLE = {latex_available_literal}
BG = "#0a0e13"
TXT = "#e8e8e0"
MUT = "#9da3aa"
ORANGE = "#e86c2f"
CYAN = "#7dd3fc"
GOLD = "#f2b84b"


def fit_width(mob, width):
    if width and mob.width > width:
        mob.scale_to_fit_width(width)
    return mob


def math_or_text(value, font_size=36, color=TXT, width=4.8):
    text = str(value or "").strip()
    if not text:
        return Text("", font_size=font_size, color=color)
    if not LATEX_AVAILABLE:
        mob = Text(text, font_size=max(18, int(font_size * 0.62)), color=color)
        return fit_width(mob, width)
    try:
        mob = MathTex(text, font_size=font_size, color=color)
    except Exception:
        mob = Text(text, font_size=max(18, int(font_size * 0.62)), color=color)
    return fit_width(mob, width)


def bullet_group(items, color=TXT, width=4.0):
    rows = []
    for item in items:
        dot = Dot(radius=0.05, color=ORANGE)
        line = Text(str(item), font_size=24, color=color).scale_to_fit_width(width)
        row = VGroup(dot, line).arrange(RIGHT, buff=0.16, aligned_edge=UP)
        rows.append(row)
    return VGroup(*rows).arrange(DOWN, buff=0.26, aligned_edge=LEFT)


def card_box(label, width=2.7, height=1.35, accent=ORANGE):
    box = RoundedRectangle(corner_radius=0.18, width=width, height=height, stroke_color=accent, stroke_width=2.6, fill_color="#15191f", fill_opacity=0.92)
    text = Text(str(label), font_size=26, color=TXT).scale_to_fit_width(width - 0.35)
    text.move_to(box.get_center())
    return VGroup(box, text)


def term_label_group(items, width=3.7):
    rows = []
    for item in items:
        term_text = str(item.get("term", "")).strip()
        meaning_text = str(item.get("meaning", "")).strip()
        if not term_text or not meaning_text:
            continue
        badge = RoundedRectangle(corner_radius=0.14, width=0.95, height=0.5, stroke_color=GOLD, stroke_width=2.2, fill_color="#1b1f24", fill_opacity=0.94)
        badge_text = Text(term_text, font_size=20, color=GOLD)
        fit_width(badge_text, 0.72)
        badge_text.move_to(badge.get_center())
        meaning = Text(meaning_text, font_size=19, color=TXT)
        fit_width(meaning, width)
        row = VGroup(VGroup(badge, badge_text), meaning).arrange(RIGHT, buff=0.18, aligned_edge=UP)
        rows.append(row)
    return VGroup(*rows).arrange(DOWN, buff=0.22, aligned_edge=LEFT) if rows else VGroup()


class {scene_name}(Scene):
    def construct(self):
        payload = PAYLOAD
        self.camera.background_color = BG
        title = Text(payload.get("title", "Lesson frame"), font_size=34, color=TXT).to_edge(UP)
        subtitle = Text(payload.get("subtitle", ""), font_size=22, color=MUT).next_to(title, DOWN, buff=0.18)
        heading = VGroup(title, subtitle)
        self.play(FadeIn(heading, shift=DOWN), run_time=0.6)

        scene_type = payload.get("scene_type")
        duration = float(payload.get("duration_sec", 6.0))
        spent = 0.6

        if scene_type == "process_flow":
            cards = VGroup(*[card_box(step, width=2.55, height=1.18, accent=ORANGE if idx == 0 else CYAN) for idx, step in enumerate(payload.get("steps", []))]).arrange(RIGHT, buff=0.42).scale(0.92)
            cards.move_to(ORIGIN + DOWN * 0.2)
            arrows = VGroup(*[Arrow(cards[i].get_right(), cards[i + 1].get_left(), buff=0.15, color=GOLD, stroke_width=5) for i in range(len(cards) - 1)])
            for idx, card in enumerate(cards):
                self.play(FadeIn(card, shift=UP * 0.1), run_time=0.35)
                spent += 0.35
                if idx < len(arrows):
                    self.play(Create(arrows[idx]), run_time=0.28)
                    spent += 0.28
            self.wait(max(0.6, duration - spent))
        elif scene_type == "equation_steps":
            equation_lines = payload.get("equation_lines", []) or [payload.get("start_equation", "start")]
            primary = math_or_text(equation_lines[0], font_size=42, color=TXT, width=5.4)
            primary_frame = SurroundingRectangle(primary, buff=0.28, corner_radius=0.18, color=ORANGE, stroke_width=2.8)
            primary_group = VGroup(primary_frame, primary).move_to(LEFT * 1.6 + UP * 0.55)
            focus = bullet_group(payload.get("focus_points", []), width=4.2).scale(0.82).to_edge(DOWN, buff=0.65)
            terms = term_label_group(payload.get("term_labels", []), width=3.25).scale(0.9).to_edge(RIGHT, buff=0.55).shift(DOWN * 0.2)
            self.play(FadeIn(primary_frame), Write(primary), run_time=0.55)
            spent += 0.55
            active_group = primary_group

            if len(equation_lines) > 1 and str(equation_lines[1]).strip() and str(equation_lines[1]).strip() != str(equation_lines[0]).strip():
                arrow = Arrow(primary_group.get_bottom() + RIGHT * 0.3, DOWN * 0.2 + RIGHT * 0.15, buff=0.14, color=GOLD, stroke_width=5.4)
                label = Text(payload.get("transform_label", "transform"), font_size=22, color=GOLD).next_to(arrow, RIGHT, buff=0.14)
                secondary = math_or_text(equation_lines[1], font_size=40, color=TXT, width=5.4)
                secondary_frame = SurroundingRectangle(secondary, buff=0.28, corner_radius=0.18, color=CYAN, stroke_width=2.6)
                secondary_group = VGroup(secondary_frame, secondary).move_to(LEFT * 1.25 + DOWN * 0.75)
                self.play(Create(arrow), FadeIn(label, shift=UP * 0.08), run_time=0.35)
                spent += 0.35
                self.play(ReplacementTransform(primary_group.copy(), secondary_group), run_time=0.65)
                spent += 0.65
                active_group = secondary_group
            if len(terms):
                self.play(LaggedStart(*[FadeIn(row, shift=LEFT * 0.08) for row in terms], lag_ratio=0.12), run_time=0.65)
                spent += 0.65
            if len(focus):
                self.play(LaggedStart(*[FadeIn(row, shift=UP * 0.08) for row in focus], lag_ratio=0.1), run_time=0.65)
                spent += 0.65
            self.play(Indicate(active_group[1], color=GOLD), run_time=0.35)
            spent += 0.35
            self.wait(max(0.6, duration - spent))
        elif scene_type == "number_line_steps":
            line = NumberLine(
                x_range=[payload.get("min_value", -2), payload.get("max_value", 6), payload.get("step", 1)],
                length=9.2,
                include_numbers=True,
                color=CYAN,
            ).shift(DOWN * 0.35)
            emphasis = Text(payload.get("emphasis", ""), font_size=22, color=GOLD).next_to(line, DOWN, buff=0.35)
            self.play(Create(line), run_time=0.65)
            spent += 0.65
            interval_anims = []
            for item in payload.get("intervals", []):
                start = line.n2p(item.get("start", 0))
                end = line.n2p(item.get("end", 0))
                segment = Line(start, end, color=item.get("color", CYAN), stroke_width=9).set_opacity(0.85)
                label = Text(item.get("label", ""), font_size=20, color=item.get("color", CYAN)).next_to(segment, UP, buff=0.14)
                interval_anims.append((segment, label))
            for segment_line, label in interval_anims:
                self.play(Create(segment_line), FadeIn(label, shift=UP * 0.08), run_time=0.35)
                spent += 0.35
            points = []
            point_labels = []
            for item in payload.get("points", []):
                dot = Dot(line.n2p(item.get("value", 0)), radius=0.08, color=item.get("color", ORANGE))
                lbl = Text(item.get("label", ""), font_size=20, color=item.get("color", ORANGE)).next_to(dot, UP, buff=0.16)
                points.append(dot)
                point_labels.append(lbl)
            self.play(LaggedStart(*[FadeIn(dot, scale=0.7) for dot in points], lag_ratio=0.15), LaggedStart(*[FadeIn(lbl, shift=UP * 0.08) for lbl in point_labels], lag_ratio=0.15), FadeIn(emphasis, shift=UP * 0.08), run_time=0.9)
            spent += 0.9
            self.wait(max(0.6, duration - spent))
        elif scene_type == "matrix_heatmap":
            values = payload.get("values", [])
            rows = len(values)
            cols = max((len(row) for row in values), default=0)
            cell_size = 0.86
            fill_palette = ["#17202a", "#244056", "#3b6278", "#5b88a1", ORANGE]
            cells = VGroup()
            numbers = VGroup()
            for r, row in enumerate(values):
                for c, value in enumerate(row):
                    fill = fill_palette[min(len(fill_palette) - 1, max(0, int(round(float(value) * (len(fill_palette) - 1)))))]
                    rect = Square(side_length=cell_size, stroke_color=CYAN, stroke_width=2, fill_color=fill, fill_opacity=0.92)
                    rect.move_to(RIGHT * ((c - (cols - 1) / 2) * cell_size) + DOWN * ((r - (rows - 1) / 2) * cell_size) + DOWN * 0.18)
                    num = Text(f"{{value:.2f}}", font_size=20, color=TXT).move_to(rect.get_center())
                    cells.add(rect)
                    numbers.add(num)
            row_labels = VGroup(*[
                Text(label, font_size=18, color=MUT).next_to(cells[idx * cols], LEFT, buff=0.18)
                for idx, label in enumerate(payload.get("row_labels", [])[:rows])
            ]) if rows and cols else VGroup()
            col_labels = VGroup(*[
                Text(label, font_size=18, color=MUT).next_to(cells[idx], UP, buff=0.18)
                for idx, label in enumerate(payload.get("col_labels", [])[:cols])
            ]) if rows and cols else VGroup()
            emphasis = Text(payload.get("emphasis", ""), font_size=22, color=GOLD).next_to(cells, DOWN, buff=0.38)
            self.play(LaggedStart(*[FadeIn(cell, scale=0.9) for cell in cells], lag_ratio=0.06), run_time=0.7)
            spent += 0.7
            self.play(LaggedStart(*[FadeIn(num, shift=UP * 0.04) for num in numbers], lag_ratio=0.04), FadeIn(row_labels), FadeIn(col_labels), FadeIn(emphasis, shift=UP * 0.08), run_time=0.6)
            spent += 0.6
            self.wait(max(0.6, duration - spent))
        elif scene_type == "cycle_loop":
            nodes = payload.get("nodes", [])
            radius = 2.15
            node_groups = VGroup()
            arrows = VGroup()
            for idx, item in enumerate(nodes):
                angle = (TAU * idx / max(1, len(nodes))) + PI / 2
                card = card_box(item, width=2.6, height=1.0, accent=ORANGE if idx == 0 else CYAN).scale(0.85)
                card.move_to([radius * np.cos(angle), radius * np.sin(angle) - 0.18, 0])
                node_groups.add(card)
            for idx in range(len(node_groups)):
                start = node_groups[idx].get_center()
                end = node_groups[(idx + 1) % len(node_groups)].get_center()
                arrows.add(CurvedArrow(start_point=start, end_point=end, angle=-PI / 3, color=GOLD, stroke_width=4.5))
            center = Circle(radius=0.82, color=GOLD, stroke_width=3).set_fill("#12181f", opacity=0.94)
            center_text = Text(payload.get("center_label", "Cycle"), font_size=24, color=GOLD).move_to(center.get_center())
            relation = Text(payload.get("relationship", ""), font_size=22, color=MUT).next_to(center, DOWN, buff=1.1)
            self.play(FadeIn(center), FadeIn(center_text), run_time=0.45)
            spent += 0.45
            self.play(LaggedStart(*[FadeIn(node, scale=0.92) for node in node_groups], lag_ratio=0.16), run_time=0.75)
            spent += 0.75
            self.play(LaggedStart(*[Create(arrow) for arrow in arrows], lag_ratio=0.12), FadeIn(relation, shift=UP * 0.08), run_time=0.65)
            spent += 0.65
            self.wait(max(0.6, duration - spent))
        elif scene_type == "comparison_cards":
            left_title = Text(payload.get("left_title", "Side A"), font_size=28, color=ORANGE)
            left_body = bullet_group(payload.get("left_points", []), width=4.0)
            left = VGroup(left_title, left_body).arrange(DOWN, buff=0.32, aligned_edge=LEFT)
            left_box = RoundedRectangle(corner_radius=0.2, width=4.6, height=3.6, stroke_color=ORANGE, fill_color="#15191f", fill_opacity=0.9)
            left.move_to(left_box.get_center())
            left_group = VGroup(left_box, left).move_to(LEFT * 3.1 + DOWN * 0.2)

            right_title = Text(payload.get("right_title", "Side B"), font_size=28, color=CYAN)
            right_body = bullet_group(payload.get("right_points", []), width=4.0)
            right = VGroup(right_title, right_body).arrange(DOWN, buff=0.32, aligned_edge=LEFT)
            right_box = RoundedRectangle(corner_radius=0.2, width=4.6, height=3.6, stroke_color=CYAN, fill_color="#15191f", fill_opacity=0.9)
            right.move_to(right_box.get_center())
            right_group = VGroup(right_box, right).move_to(RIGHT * 3.1 + DOWN * 0.2)

            arrow = DoubleArrow(left_group.get_right(), right_group.get_left(), buff=0.25, color=GOLD, stroke_width=5)
            relation = Text(payload.get("relationship", ""), font_size=22, color=GOLD).next_to(arrow, UP, buff=0.18)
            self.play(FadeIn(left_group, shift=RIGHT * 0.15), FadeIn(right_group, shift=LEFT * 0.15), run_time=0.7)
            spent += 0.7
            self.play(Create(arrow), FadeIn(relation, shift=UP * 0.1), run_time=0.45)
            spent += 0.45
            self.wait(max(0.6, duration - spent))
        elif scene_type == "axes_curve":
            x_range = payload.get("x_range", [-4, 4, 1])
            y_range = payload.get("y_range", [-2, 5, 1])
            axes = Axes(x_range=x_range, y_range=y_range, x_length=6.9, y_length=4.5, axis_config={{"color": CYAN}})
            axes.to_edge(LEFT, buff=0.55).shift(DOWN * 0.12)
            x_label = Text(payload.get("x_label", "x"), font_size=22, color=CYAN).next_to(axes.x_axis, RIGHT)
            y_label = Text(payload.get("y_label", "y"), font_size=22, color=CYAN).next_to(axes.y_axis, UP)
            pairs = payload.get("point_pairs", [])
            curve_kind = str(payload.get("curve_kind", "smooth")).strip().lower()
            points = [axes.c2p(pair[0], pair[1]) for pair in pairs]
            if len(points) < 2:
                points = [axes.c2p(0, 0), axes.c2p(1, 1)]
                pairs = [[0, 0], [1, 1]]
            if len(pairs) >= 3 and curve_kind in ("parabola", "trajectory"):
                xs = [pair[0] for pair in pairs]
                ys = [pair[1] for pair in pairs]
                coeffs = np.polyfit(xs, ys, 2)
                path = axes.plot(lambda x: coeffs[0] * (x ** 2) + coeffs[1] * x + coeffs[2], x_range=[min(xs), max(xs)], color=ORANGE, stroke_width=5.2)
            elif len(pairs) >= 2 and curve_kind == "line":
                xs = [pair[0] for pair in pairs]
                ys = [pair[1] for pair in pairs]
                coeffs = np.polyfit(xs, ys, 1)
                path = axes.plot(lambda x: coeffs[0] * x + coeffs[1], x_range=[min(xs), max(xs)], color=ORANGE, stroke_width=5.2)
            else:
                path = VMobject(color=ORANGE, stroke_width=5.2)
                path.set_points_smoothly(points if len(points) >= 3 else [points[0], points[0] + RIGHT * 0.01, points[-1]])
            default_highlights = []
            if pairs:
                mid_idx = len(pairs) // 2
                default_highlights = [
                    {{"x": pairs[0][0], "y": pairs[0][1], "label": "start", "color": ORANGE}},
                    {{"x": pairs[mid_idx][0], "y": pairs[mid_idx][1], "label": "key point", "color": GOLD}},
                    {{"x": pairs[-1][0], "y": pairs[-1][1], "label": "end", "color": CYAN}},
                ]
            highlight_points = payload.get("highlight_points", []) or default_highlights
            marker_dots = VGroup()
            marker_labels = VGroup()
            guide_lines = VGroup()
            for item in highlight_points[:3]:
                point = axes.c2p(item.get("x", 0), item.get("y", 0))
                color = item.get("color", GOLD)
                dot = Dot(point, color=color, radius=0.075)
                marker_dots.add(dot)
                marker_labels.add(Text(item.get("label", "point"), font_size=19, color=color).next_to(dot, UP if item.get("y", 0) >= 0 else DOWN, buff=0.12))
                guide_lines.add(DashedLine(axes.c2p(item.get("x", 0), y_range[0]), point, dash_length=0.12, color=color, stroke_opacity=0.45))
                guide_lines.add(DashedLine(axes.c2p(x_range[0], item.get("y", 0)), point, dash_length=0.12, color=color, stroke_opacity=0.45))
            tag = Text(payload.get("graph_label", ""), font_size=22, color=GOLD).next_to(axes, DOWN, buff=0.28)
            relation = Text(payload.get("relationship", ""), font_size=20, color=MUT)
            fit_width(relation, 4.1)
            formula_group = VGroup()
            formula_text = str(payload.get("curve_formula", "") or "").strip()
            if formula_text:
                formula = math_or_text(formula_text, font_size=32, color=GOLD, width=4.1)
                formula_frame = SurroundingRectangle(formula, buff=0.22, corner_radius=0.16, color=GOLD, stroke_width=2.3)
                formula_group = VGroup(formula_frame, formula).to_edge(RIGHT, buff=0.45).shift(UP * 1.05)
            term_group = term_label_group(payload.get("term_labels", []), width=3.15).scale(0.9).to_edge(RIGHT, buff=0.45).shift(DOWN * 0.45)
            if len(term_group):
                relation.next_to(term_group, DOWN, buff=0.24)
            elif len(formula_group):
                relation.next_to(formula_group, DOWN, buff=0.3)
            else:
                relation.next_to(axes, RIGHT, buff=0.45)
            mover = Dot(points[0], radius=0.08, color=GOLD)
            self.play(Create(axes), FadeIn(x_label), FadeIn(y_label), run_time=0.7)
            spent += 0.7
            if len(formula_group):
                self.play(FadeIn(formula_group, shift=LEFT * 0.08), run_time=0.35)
                spent += 0.35
            self.play(Create(path), FadeIn(tag, shift=UP * 0.1), run_time=0.85)
            spent += 0.85
            self.play(FadeIn(mover, scale=0.7), MoveAlongPath(mover, path), run_time=0.95 if curve_kind in ("smooth", "parabola", "trajectory") else 0.6, rate_func=linear)
            spent += 0.95 if curve_kind in ("smooth", "parabola", "trajectory") else 0.6
            marker_anims = [FadeIn(dot, scale=0.7) for dot in marker_dots]
            marker_anims.extend(FadeIn(line) for line in guide_lines)
            label_anims = [FadeIn(label, shift=UP * 0.06) for label in marker_labels]
            extras = []
            if len(term_group):
                extras.append(LaggedStart(*[FadeIn(row, shift=LEFT * 0.06) for row in term_group], lag_ratio=0.12))
            if relation.text:
                extras.append(FadeIn(relation, shift=UP * 0.08))
            self.play(LaggedStart(*marker_anims, lag_ratio=0.08), LaggedStart(*label_anims, lag_ratio=0.08), *extras, run_time=0.9)
            spent += 0.9
            if len(formula_group):
                self.play(Indicate(formula_group[1], color=GOLD), run_time=0.35)
                spent += 0.35
            self.wait(max(0.6, duration - spent))
        elif scene_type == "vector_axes":
            plane = NumberPlane(x_range=[-4, 4, 1], y_range=[-3, 4, 1], background_line_style={{"stroke_color": "#20303c", "stroke_width": 1.0, "stroke_opacity": 0.45}})
            plane.scale(0.85).shift(DOWN * 0.15)
            x_label = Text(payload.get("x_label", "x"), font_size=22, color=CYAN).next_to(plane.x_axis, RIGHT)
            y_label = Text(payload.get("y_label", "y"), font_size=22, color=CYAN).next_to(plane.y_axis, UP)
            vectors = []
            tags = []
            for item in payload.get("vectors", []):
                vec = Arrow(plane.c2p(0, 0), plane.c2p(item.get("x", 1), item.get("y", 1)), buff=0, color=item.get("color", ORANGE), stroke_width=6)
                tag = Text(item.get("label", "vector"), font_size=20, color=item.get("color", ORANGE)).next_to(vec.get_end(), UP if item.get("y", 1) >= 0 else DOWN, buff=0.14)
                vectors.append(vec)
                tags.append(tag)
            self.play(Create(plane), FadeIn(x_label), FadeIn(y_label), run_time=0.7)
            spent += 0.7
            self.play(LaggedStart(*[GrowArrow(vec) for vec in vectors], lag_ratio=0.18), LaggedStart(*[FadeIn(tag, shift=UP * 0.08) for tag in tags], lag_ratio=0.18), run_time=1.0)
            spent += 1.0
            self.wait(max(0.6, duration - spent))
        elif scene_type == "geometry_triangle":
            tri = Polygon(LEFT * 3 + DOWN * 2, RIGHT * 3 + DOWN * 2, UP * 2.2, color=ORANGE, stroke_width=5).scale(0.9)
            dots = [Dot(point, color=GOLD, radius=0.07) for point in tri.get_vertices()]
            labels = [Text(label, font_size=24, color=TXT).next_to(dot, direction, buff=0.12) for label, dot, direction in zip(payload.get("labels", ["A", "B", "C"]), dots, [DL, DR, UP])]
            emphasis = Text(payload.get("emphasis", ""), font_size=22, color=CYAN).next_to(tri, DOWN, buff=0.35)
            self.play(Create(tri), run_time=0.8)
            spent += 0.8
            self.play(LaggedStart(*[FadeIn(dot, scale=0.7) for dot in dots], lag_ratio=0.15), LaggedStart(*[FadeIn(label, shift=UP * 0.08) for label in labels], lag_ratio=0.15), run_time=0.55)
            spent += 0.55
            self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.35)
            spent += 0.35
            self.wait(max(0.6, duration - spent))
        else:
            cards = VGroup(*[card_box(item, width=5.6, height=1.05, accent=ORANGE if idx == 0 else CYAN) for idx, item in enumerate(payload.get("cards", []))]).arrange(DOWN, buff=0.35)
            cards.move_to(DOWN * 0.25)
            self.play(LaggedStart(*[FadeIn(card, shift=UP * 0.12) for card in cards], lag_ratio=0.18), run_time=1.0)
            spent += 1.0
            self.wait(max(0.6, duration - spent))
'''


def locate_rendered_video(work_dir: Path, output_name: str) -> Path | None:
    exact_matches = [path for path in work_dir.rglob(output_name) if path.is_file()]
    if exact_matches:
        return max(exact_matches, key=lambda path: path.stat().st_size if path.exists() else 0)
    mp4s = [path for path in work_dir.rglob("*.mp4") if path.is_file()]
    if not mp4s:
        return None
    try:
        return max(mp4s, key=lambda path: path.stat().st_mtime)
    except Exception:
        return max(mp4s, key=lambda path: path.stat().st_size if path.exists() else 0)


def debug_bundle_dir(key: str) -> Path:
    path = MANIM_LOG_DIR / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def tail_lines(text: Any, line_count: int) -> str:
    lines = str(text or "").splitlines()
    return "\n".join(lines[-line_count:]) if lines else ""


def read_log_tail(path_text: str | Path | None, line_count: int) -> str:
    if not path_text:
        return ""
    try:
        path = Path(path_text)
        if path.exists():
            return tail_lines(path.read_text(encoding="utf-8", errors="replace"), line_count)
    except Exception:
        return ""
    return ""


def manim_failure_stage(payload: dict[str, Any] | None) -> str:
    data = payload or {}
    if data.get("_render_fallback_retry") or data.get("fallback_attempted") or data.get("render_failure_stage") == "fallback":
        return "fallback"
    source = clean_spaces(data.get("manim_code_source")).lower()
    if data.get("repair_attempted") or data.get("repair_used") or "repair" in source:
        return "repaired"
    return "generated"


def repair_was_attempted(payload: dict[str, Any] | None) -> bool:
    data = payload or {}
    source = clean_spaces(data.get("manim_code_source")).lower()
    return bool(data.get("repair_attempted") or data.get("repair_used") or "repair" in source)


def fallback_was_attempted(payload: dict[str, Any] | None) -> bool:
    data = payload or {}
    return bool(data.get("_render_fallback_retry") or data.get("fallback_attempted") or data.get("_fallback_will_run") or data.get("render_failure_stage") == "fallback")


def summarize_manim_error(stderr: Any, stdout: Any, fallback: str = "") -> str:
    combined = "\n".join([str(stderr or ""), str(stdout or "")])
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    priority_tokens = (
        "traceback",
        "exception",
        "error",
        "typeerror",
        "valueerror",
        "attributeerror",
        "nameerror",
        "indexerror",
        "runtimeerror",
        "modulenotfounderror",
    )
    for line in reversed(lines):
        lowered = line.lower()
        if any(token in lowered for token in priority_tokens):
            return line[-900:]
    if lines:
        return lines[-1][-900:]
    return clean_spaces(fallback)[-900:]


def log_manim_failure(
    *,
    key: str,
    segment_id: str,
    frame_number: int | None,
    scene_file: Path,
    command_text: str,
    return_code: int | str | None,
    payload: dict[str, Any] | None,
    debug_paths: dict[str, str],
    error_summary: str,
    fallback_will_run: bool = False,
) -> None:
    stderr_tail = read_log_tail(debug_paths.get("stderr_path"), MANIM_STDERR_TAIL_LINES)
    stdout_tail = read_log_tail(debug_paths.get("stdout_path"), MANIM_STDOUT_TAIL_LINES)
    fallback_attempted = fallback_was_attempted(payload) or fallback_will_run
    logger.error(
        "[manim] render failed stage=%s segment=%s frame=%s render_id=%s return_code=%s repair_attempted=%s fallback_attempted=%s scene_file=%s scene_code_path=%s command=%s stderr_log=%s stdout_log=%s debug_meta=%s error_summary=%s",
        manim_failure_stage(payload),
        segment_id,
        frame_number,
        key,
        return_code,
        repair_was_attempted(payload),
        fallback_attempted,
        scene_file,
        scene_file,
        command_text,
        debug_paths.get("stderr_path"),
        debug_paths.get("stdout_path"),
        debug_paths.get("meta_path"),
        error_summary,
    )
    if stderr_tail:
        logger.error(
            "[manim] stderr tail stage=%s segment=%s frame=%s last_lines=%s\n%s",
            manim_failure_stage(payload),
            segment_id,
            frame_number,
            MANIM_STDERR_TAIL_LINES,
            stderr_tail,
        )
    if stdout_tail:
        logger.error(
            "[manim] stdout tail stage=%s segment=%s frame=%s last_lines=%s\n%s",
            manim_failure_stage(payload),
            segment_id,
            frame_number,
            MANIM_STDOUT_TAIL_LINES,
            stdout_tail,
        )


def persist_debug_artifacts(
    *,
    key: str,
    segment_id: str,
    frame_number: int | None,
    scene_file: Path,
    command: list[str],
    payload: dict[str, Any] | None,
    stdout: str,
    stderr: str,
    status: str,
    output_path: Path | None = None,
    return_code: int | str | None = None,
    error_summary: str = "",
) -> dict[str, str]:
    bundle_dir = debug_bundle_dir(key)
    stdout_path = bundle_dir / "stdout.log"
    stderr_path = bundle_dir / "stderr.log"
    command_path = bundle_dir / "command.txt"
    meta = {
        "status": status,
        "render_id": key,
        "failure_stage": manim_failure_stage(payload),
        "error_summary": error_summary or summarize_manim_error(stderr, stdout, status),
        "return_code": return_code,
        "repair_attempted": repair_was_attempted(payload),
        "fallback_attempted": fallback_was_attempted(payload),
        "segment_id": segment_id,
        "frame_number": frame_number,
        "scene_file": str(scene_file),
        "scene_code_path": str(scene_file),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "command": command,
        "command_string": command_to_string(command),
        "python_executable": str(resolve_manim_python()),
        "runtime_info": manim_runtime_info(),
        "output_path": str(output_path) if output_path else None,
        "payload": payload,
    }
    meta_path = bundle_dir / "meta.json"
    write_text_file(meta_path, json.dumps(meta, indent=2, ensure_ascii=False))
    write_text_file(command_path, command_to_string(command))
    write_text_file(stdout_path, stdout or "")
    write_text_file(stderr_path, stderr or "")
    return {
        "bundle_dir": str(bundle_dir),
        "meta_path": str(meta_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "command_path": str(command_path),
    }


def final_manim_video_path(key: str, output_name: str) -> Path:
    if MANIM_PUBLIC_BASE_URL.startswith("/rendered-scenes"):
        return MANIM_OUTPUT_DIR / output_name
    return MANIM_OUTPUT_DIR / key / output_name


def public_artifact_paths(final_video: Path, key: str) -> tuple[Path, Path, Path]:
    bundle_dir = debug_bundle_dir(key)
    return bundle_dir / "scene.py", bundle_dir / "render.log", bundle_dir / "metadata.json"


def render_fallback_allowed(payload: dict[str, Any] | None) -> bool:
    data = payload or {}
    return not bool(data.get("_render_fallback_retry") or data.get("_disable_render_fallback"))


def fallback_render_payload(payload: dict[str, Any] | None, error: str) -> dict[str, Any]:
    validation = dict(((payload or {}).get("manim_code_validation") or {}))
    validation.update({"valid": False, "error": error, "fallback_used": True})
    return {
        **(payload or {}),
        "_render_fallback_retry": True,
        "fallback_attempted": True,
        "render_failure_stage": "fallback",
        "manim_code_validation": validation,
        "render_fallback_reason": error,
    }


def run_manim_scene(
    *,
    key: str,
    scene_name: str,
    scene_source: str,
    output_name: str,
    final_video: Path,
    segment_id: str,
    frame_number: int | None,
    payload: dict[str, Any] | None,
    work_dir: Path,
) -> dict[str, Any]:
    runtime = manim_runtime_info()
    python_exec = resolve_manim_python()
    scene_file = MANIM_SCENES_DIR / f"{key}.py"

    if not runtime.get("manim_importable"):
        logger.error(
            "manim-runtime unavailable segment=%s frame=%s python=%s version=%s error=%s",
            segment_id,
            frame_number,
            runtime.get("python_executable"),
            runtime.get("python_version"),
            runtime.get("manim_import_error"),
        )
        raise RuntimeError(
            "Manim runtime import check failed for "
            f"segment={segment_id} frame={frame_number} python={runtime.get('python_executable')} "
            f"version={runtime.get('python_version')} error={runtime.get('manim_import_error')}"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    write_text_file(scene_file, scene_source)
    try:
        validate_scene_source(scene_source, scene_file)
    except Exception as exc:
        artifact_payload = {**(payload or {}), "_fallback_will_run": render_fallback_allowed(payload)}
        debug_paths = persist_debug_artifacts(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command=[],
            payload=artifact_payload,
            stdout="",
            stderr=str(exc),
            status="scene-compile-failed",
            output_path=None,
            return_code="compile",
            error_summary=str(exc),
        )
        log_manim_failure(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command_text="",
            return_code="compile",
            payload=artifact_payload,
            debug_paths=debug_paths,
            error_summary=str(exc),
            fallback_will_run=render_fallback_allowed(payload),
        )
        raise RuntimeError(
            "Manim scene compile failed for "
            f"segment={segment_id} frame={frame_number} scene_file={scene_file} "
            f"render_id={key} failure_stage={manim_failure_stage(payload)} return_code=compile "
            f"error_summary={exc} stderr_log={debug_paths['stderr_path']} stdout_log={debug_paths['stdout_path']} debug_meta={debug_paths['meta_path']}"
        ) from exc

    command = [
        str(python_exec),
        "-m",
        "manim",
        f"-{MANIM_QUALITY}",
        str(scene_file),
        scene_name,
        "--media_dir",
        str(work_dir),
        "--output_file",
        output_name,
        "--disable_caching",
        "--format",
        "mp4",
    ]
    command_text = command_to_string(command)
    logger.info(
        "manim-render start segment=%s frame=%s scene=%s command=%s runtime_python=%s runtime_version=%s",
        segment_id,
        frame_number,
        scene_file,
        command_text,
        runtime.get("python_executable"),
        runtime.get("python_version"),
    )

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    render_started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=MANIM_RENDER_TIMEOUT_SEC,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        render_time_sec = round(time.perf_counter() - render_started, 3)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        error = f"Generated Manim render timed out after {MANIM_RENDER_TIMEOUT_SEC} seconds."
        artifact_payload = {**(payload or {}), "_fallback_will_run": render_fallback_allowed(payload)}
        debug_paths = persist_debug_artifacts(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command=command,
            payload=artifact_payload,
            stdout=stdout,
            stderr=stderr,
            status="timeout",
            output_path=None,
            return_code="timeout",
            error_summary=error,
        )
        log_manim_failure(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command_text=command_text,
            return_code="timeout",
            payload=artifact_payload,
            debug_paths=debug_paths,
            error_summary=f"{error} elapsed_sec={render_time_sec}",
            fallback_will_run=render_fallback_allowed(payload),
        )
        if render_fallback_allowed(payload):
            logger.warning("[manim] generated-code failure: %s; rendering fallback scene segment=%s frame=%s", error, segment_id, frame_number)
            fallback_payload = fallback_render_payload(payload, error)
            return run_manim_scene(
                key=key,
                scene_name=DIRECT_SCENE_CLASS_NAME,
                scene_source=fallback_direct_manim_code(fallback_payload, error),
                output_name=output_name,
                final_video=final_video,
                segment_id=segment_id,
                frame_number=frame_number,
                payload=fallback_payload,
                work_dir=work_dir,
            )
        raise RuntimeError(
            "Manim render timed out for "
            f"segment={segment_id} frame={frame_number} scene_file={scene_file} "
            f"render_id={key} failure_stage={manim_failure_stage(payload)} return_code=timeout "
            f"error_summary={error} command={command_text} "
            f"stderr_log={debug_paths['stderr_path']} stdout_log={debug_paths['stdout_path']} debug_meta={debug_paths['meta_path']}"
        ) from exc

    render_time_sec = round(time.perf_counter() - render_started, 3)
    rendered = locate_rendered_video(work_dir, output_name) if result.returncode == 0 else None
    render_status = "ok" if result.returncode == 0 and rendered else "failed"
    render_error_summary = "" if render_status == "ok" else summarize_manim_error(result.stderr or "", result.stdout or "", f"return code {result.returncode}")
    artifact_payload = {**(payload or {}), "_fallback_will_run": render_status != "ok" and render_fallback_allowed(payload)}
    debug_paths = persist_debug_artifacts(
        key=key,
        segment_id=segment_id,
        frame_number=frame_number,
        scene_file=scene_file,
        command=command,
        payload=artifact_payload,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        status=render_status,
        output_path=rendered,
        return_code=result.returncode,
        error_summary=render_error_summary,
    )

    if result.returncode != 0:
        latex_failed = latex_render_failure(result.stdout or "", result.stderr or "", scene_source)
        if render_fallback_allowed(payload):
            error = (
                "Render failed because LaTeX is unavailable or TeX rendering failed."
                if latex_failed
                else f"Generated Manim render failed with return code {result.returncode}."
            )
            error_summary = summarize_manim_error(result.stderr or "", result.stdout or "", error)
            log_manim_failure(
                key=key,
                segment_id=segment_id,
                frame_number=frame_number,
                scene_file=scene_file,
                command_text=command_text,
                return_code=result.returncode,
                payload=artifact_payload,
                debug_paths=debug_paths,
                error_summary=error_summary,
                fallback_will_run=True,
            )
            logger.warning("[manim] generated-code failure: %s; rendering fallback scene segment=%s frame=%s", error, segment_id, frame_number)
            fallback_payload = fallback_render_payload(payload, error)
            return run_manim_scene(
                key=key,
                scene_name=DIRECT_SCENE_CLASS_NAME,
                scene_source=fallback_direct_manim_code(fallback_payload, error),
                output_name=output_name,
                final_video=final_video,
                segment_id=segment_id,
                frame_number=frame_number,
                payload=fallback_payload,
                work_dir=work_dir,
            )
        error_summary = summarize_manim_error(result.stderr or "", result.stdout or "", f"return code {result.returncode}")
        log_manim_failure(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command_text=command_text,
            return_code=result.returncode,
            payload=artifact_payload,
            debug_paths=debug_paths,
            error_summary=error_summary,
            fallback_will_run=False,
        )
        raise RuntimeError(
            "Manim render failed for "
            f"segment={segment_id} frame={frame_number} scene_file={scene_file} "
            f"render_id={key} failure_stage={manim_failure_stage(payload)} return_code={result.returncode} "
            f"error_summary={error_summary} command={command_text} "
            f"stderr_log={debug_paths['stderr_path']} stdout_log={debug_paths['stdout_path']} debug_meta={debug_paths['meta_path']}"
        )

    if rendered is None or not rendered.exists():
        error = "Manim completed without producing an mp4 output."
        missing_output_payload = {**(payload or {}), "_fallback_will_run": render_fallback_allowed(payload)}
        debug_paths = persist_debug_artifacts(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command=command,
            payload=missing_output_payload,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            status="missing-output",
            output_path=None,
            return_code=result.returncode,
            error_summary=error,
        )
        log_manim_failure(
            key=key,
            segment_id=segment_id,
            frame_number=frame_number,
            scene_file=scene_file,
            command_text=command_text,
            return_code=result.returncode,
            payload=missing_output_payload,
            debug_paths=debug_paths,
            error_summary=error,
            fallback_will_run=render_fallback_allowed(payload),
        )
        if render_fallback_allowed(payload):
            logger.warning("[manim] generated-code failure: %s; rendering fallback scene segment=%s frame=%s", error, segment_id, frame_number)
            fallback_payload = fallback_render_payload(payload, error)
            return run_manim_scene(
                key=key,
                scene_name=DIRECT_SCENE_CLASS_NAME,
                scene_source=fallback_direct_manim_code(fallback_payload, error),
                output_name=output_name,
                final_video=final_video,
                segment_id=segment_id,
                frame_number=frame_number,
                payload=fallback_payload,
                work_dir=work_dir,
            )
        raise RuntimeError(
            "Manim render completed without producing an mp4 output for "
            f"segment={segment_id} frame={frame_number} scene_file={scene_file} "
            f"render_id={key} failure_stage={manim_failure_stage(payload)} error_summary={error} "
            f"command={command_text} stderr_log={debug_paths['stderr_path']} stdout_log={debug_paths['stdout_path']} debug_meta={debug_paths['meta_path']}"
        )

    final_video.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered, final_video)
    public_scene, public_log, public_meta = public_artifact_paths(final_video, key)
    shutil.copy2(scene_file, public_scene)
    write_text_file(
        public_log,
        "\n".join(
            [
                command_text,
                "",
                "STDOUT:",
                result.stdout or "",
                "",
                "STDERR:",
                result.stderr or "",
            ]
        ),
    )
    used_fallback = bool((payload or {}).get("_render_fallback_retry") or ((payload or {}).get("manim_code_validation") or {}).get("fallback_used"))
    media_url, stored_object = _publish_manim_video(final_video, payload=payload, segment_id=segment_id, key=key)
    write_text_file(
        public_meta,
        json.dumps(
            {
                "key": key,
                "scene_name": scene_name,
                "segment_id": segment_id,
                "frame_number": frame_number,
                "video_path": str(final_video),
                "video_url": media_url,
                "scene_path": str(public_scene),
                "render_log": str(public_log),
                "command": command_text,
                "render_time_sec": render_time_sec,
                "used_fallback": used_fallback,
                "storage": stored_object,
                "latex_available": has_latex_available(),
                "text_only_mode": manim_text_only_mode(),
                "layout_mode": (payload or {}).get("layout_mode"),
                "region_replacement_used": bool((payload or {}).get("region_replacement_used")),
                "visual_complexity": (payload or {}).get("visual_complexity"),
                "repair_used": bool((payload or {}).get("repair_used")),
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    logger.info(
        "[manim] render success segment=%s frame=%s elapsed_sec=%s output=%s url=%s scene=%s debug_meta=%s",
        segment_id,
        frame_number,
        render_time_sec,
        final_video,
        (stored_object or {}).get("object_key") or media_url,
        scene_file,
        debug_paths["meta_path"],
    )
    return {
        "media_url": media_url,
        "video_url": media_url,
        "public_url": media_url,
        "media_path": str(final_video),
        "scene_source_path": str(public_scene),
        "render_log_path": str(public_log),
        "metadata_path": str(public_meta),
        "stdout_path": debug_paths["stdout_path"],
        "stderr_path": debug_paths["stderr_path"],
        "debug_meta_path": debug_paths["meta_path"],
        "render_time_sec": render_time_sec,
        "cache_hit": False,
        "used_fallback": used_fallback,
        "storage": stored_object,
        "payload": payload,
        "layout_mode": (payload or {}).get("layout_mode"),
        "region_replacement_used": bool((payload or {}).get("region_replacement_used")),
        "visual_complexity": (payload or {}).get("visual_complexity"),
        "repair_used": bool((payload or {}).get("repair_used")),
    }


def render_manim_payload(
    payload: dict[str, Any],
    *,
    segment_id: str | None = None,
    frame_number: int | None = None,
) -> dict[str, Any]:
    if not MANIM_ENABLED:
        raise RuntimeError("Manim is disabled via MANIM_ENABLED=0; no visual will be rendered.")
    key = payload_hash(payload)
    direct_source = clean_spaces(payload.get("renderer_version")) == DIRECT_MANIM_RENDERER_VERSION
    validation: dict[str, Any] | None = None
    if direct_source:
        scene_name = normalize_direct_scene_class_name(payload.get("scene_class_name"))
        scene_source, validation = prepare_direct_manim_code(
            str(payload.get("manim_code") or ""),
            payload,
            scene_class_name=scene_name,
        )
        payload = {**payload, "scene_class_name": scene_name, "manim_code_validation": validation}
    else:
        scene_name = f"ParalleaScene{key}"
        scene_source = build_scene_source(scene_name, payload)
    if not scene_name:
        scene_name = f"ParalleaScene{key}"
    output_name = f"{key}.mp4" if MANIM_PUBLIC_BASE_URL.startswith("/rendered-scenes") else "scene.mp4"
    final_video = final_manim_video_path(key, output_name)
    work_dir = MANIM_WORK_DIR / key
    segment_label = clean_spaces(segment_id) or payload.get("segment_id") or f"frame_{frame_number or 0}"

    if not storage_enabled() and final_video.exists() and final_video.stat().st_size > 0:
        scene_file, render_log, metadata_file = public_artifact_paths(final_video, key)
        cache_bust = int(final_video.stat().st_mtime)
        media_url = f"{path_to_public_url(final_video)}?v={cache_bust}"
        logger.info("[manim] cache hit segment=%s frame=%s output=%s url=%s", segment_label, frame_number, final_video, media_url)
        return {
            "media_url": media_url,
            "video_url": media_url,
            "public_url": media_url,
            "media_path": str(final_video),
            "scene_source_path": str(scene_file) if scene_file.exists() else None,
            "render_log_path": str(render_log) if render_log.exists() else None,
            "metadata_path": str(metadata_file) if metadata_file.exists() else None,
            "used_fallback": (validation or {}).get("fallback_used", False),
            "cache_hit": True,
            "render_time_sec": 0.0,
            "payload": payload,
            "validation": validation,
            "layout_mode": payload.get("layout_mode"),
            "region_replacement_used": bool(payload.get("region_replacement_used")),
            "visual_complexity": payload.get("visual_complexity"),
            "repair_used": bool(payload.get("repair_used")),
        }

    return run_manim_scene(
        key=key,
        scene_name=scene_name,
        scene_source=scene_source,
        output_name=output_name,
        final_video=final_video,
        segment_id=segment_label,
        frame_number=frame_number,
        payload=payload,
        work_dir=work_dir,
    )


def render_manim_healthcheck() -> dict[str, Any]:
    latex_available = has_latex_available()
    if MANIM_REQUIRE_LATEX and not latex_available:
        info = latex_runtime_info()
        raise RuntimeError(
            "MANIM_REQUIRE_LATEX=1 but LaTeX is unavailable. "
            f"latex_path={info.get('latex_path') or '<not found>'} "
            f"dvisvgm_path={info.get('dvisvgm_path') or '<not found>'}"
        )
    output_name = "test.mp4" if MANIM_PUBLIC_BASE_URL.startswith("/rendered-scenes") else "scene.mp4"
    scene_name = DIRECT_SCENE_CLASS_NAME
    text_key = "test"
    text_final_video = final_manim_video_path(text_key, output_name)
    text_scene_source = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Manim Render Test")
        circle = Circle()
        self.play(Write(title))
        self.play(Create(circle))
        self.wait(1)
"""
    text_result = run_manim_scene(
        key=text_key,
        scene_name=scene_name,
        scene_source=text_scene_source,
        output_name=output_name,
        final_video=text_final_video,
        segment_id="healthcheck_text",
        frame_number=0,
        payload={"scene_type": "healthcheck_text", "_disable_render_fallback": True},
        work_dir=MANIM_HEALTH_DIR,
    )
    mathtex_result: dict[str, Any] | None = None
    mathtex_error = ""
    mathtex_allowed = manim_mathtex_allowed()
    if mathtex_allowed:
        math_key = "test_mathtex"
        math_output_name = "test_mathtex.mp4" if MANIM_PUBLIC_BASE_URL.startswith("/rendered-scenes") else output_name
        math_final_video = final_manim_video_path(math_key, math_output_name)
        math_scene_source = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("MathTex Render Test")
        equation = MathTex(r"v = u + at")
        equation.next_to(title, DOWN, buff=0.7)
        self.play(Write(title))
        self.play(Write(equation))
        self.wait(1)
"""
        try:
            mathtex_result = run_manim_scene(
                key=math_key,
                scene_name=scene_name,
                scene_source=math_scene_source,
                output_name=math_output_name,
                final_video=math_final_video,
                segment_id="healthcheck_mathtex",
                frame_number=1,
                payload={"scene_type": "healthcheck_mathtex", "_disable_render_fallback": True},
                work_dir=MANIM_HEALTH_DIR / "mathtex",
            )
        except Exception as exc:  # noqa: BLE001
            mathtex_error = str(exc)
            logger.exception("[manim] MathTex healthcheck failed: %s", exc)
    return {
        **text_result,
        "latex_available": latex_available,
        "latex_path": latex_runtime_info().get("latex_path") or "",
        "dvisvgm_available": bool(latex_runtime_info().get("dvisvgm_available")),
        "dvisvgm_path": latex_runtime_info().get("dvisvgm_path") or "",
        "manim_allow_mathtex": MANIM_ALLOW_MATHTEX,
        "manim_allow_mathtex_effective": manim_allow_mathtex_effective_value(),
        "text_scene_rendered": Path(text_result.get("media_path") or "").exists(),
        "text_scene": text_result,
        "mathtex_scene_rendered": bool(mathtex_result and Path(mathtex_result.get("media_path") or "").exists()),
        "mathtex_scene_skipped": not mathtex_allowed,
        "mathtex_scene_skipped_reason": "" if mathtex_allowed else manim_allow_mathtex_effective_value(),
        "mathtex_scene": mathtex_result,
        "mathtex_error": mathtex_error,
    }
