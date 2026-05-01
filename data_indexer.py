from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from config import DATA_DIR

try:
    import faiss
except Exception:
    faiss = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None


logger = logging.getLogger("parallea.indexer")
MODEL_NAME = "all-MiniLM-L6-v2"
_encoder: SentenceTransformer | None = None


def _video_dir(video_id: str) -> Path:
    raw = str(video_id or "").strip()
    folder = raw if raw.startswith("video_") else f"video_{raw}"
    return DATA_DIR / folder


def _chunks_path(video_id: str) -> Path:
    return _video_dir(video_id) / "chunks.json"


def _index_path(video_id: str) -> Path:
    return _video_dir(video_id) / "index.faiss"


def _embeddings_path(video_id: str) -> Path:
    return _video_dir(video_id) / "embeddings.npy"


def _load_encoder() -> SentenceTransformer | None:
    global _encoder
    if _encoder is not None:
        return _encoder
    if SentenceTransformer is None:
        logger.warning("sentence-transformers is unavailable; vector indexing is disabled")
        return None
    _encoder = SentenceTransformer(MODEL_NAME)
    return _encoder


def _load_chunks(video_id: str) -> list[dict[str, Any]]:
    path = _chunks_path(video_id)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("failed to read transcript chunks for video_id=%s", video_id)
        return []
    rows: list[dict[str, Any]] = []
    for key in sorted(raw.keys(), key=lambda item: int(item)):
        item = raw[key] if isinstance(raw.get(key), dict) else {}
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        rows.append(
            {
                "index": int(key),
                "start_sec": float(item.get("start_sec", 0.0)),
                "end_sec": float(item.get("end_sec", 0.0)),
                "text": text,
            }
        )
    return rows


def _encode_texts(texts: list[str]) -> np.ndarray:
    encoder = _load_encoder()
    if encoder is None:
        return np.empty((0, 0), dtype=np.float32)
    embeddings = encoder.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return np.asarray(embeddings, dtype=np.float32)


def build_index(video_id: str) -> bool:
    if faiss is None:
        logger.warning("faiss is unavailable; skipping vector index build for video_id=%s", video_id)
        return False
    chunks = _load_chunks(video_id)
    if not chunks:
        return False
    texts = [item["text"] for item in chunks]
    embeddings = _encode_texts(texts)
    if embeddings.size == 0:
        return False
    video_dir = _video_dir(video_id)
    video_dir.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexFlatL2(int(embeddings.shape[1]))
    index.add(embeddings)
    faiss.write_index(index, str(_index_path(video_id)))
    np.save(_embeddings_path(video_id), embeddings)
    logger.info("vector index built video_id=%s chunks=%s dim=%s", video_id, len(chunks), embeddings.shape[1])
    return True


def query_index(video_id: str, question_text: str, top_k: int = 3) -> list[dict[str, Any]]:
    if faiss is None:
        return []
    index_path = _index_path(video_id)
    if not index_path.exists():
        return []
    chunks = _load_chunks(video_id)
    if not chunks:
        return []
    try:
        index = faiss.read_index(str(index_path))
    except Exception:
        logger.exception("failed to load faiss index for video_id=%s", video_id)
        return []
    query_embedding = _encode_texts([" ".join(str(question_text or "").split())])
    if query_embedding.size == 0:
        return []
    limit = max(1, min(int(top_k or 3), len(chunks)))
    distances, indices = index.search(query_embedding, limit)
    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for chunk_idx, distance in zip(indices[0].tolist(), distances[0].tolist()):
        if chunk_idx < 0 or chunk_idx >= len(chunks) or chunk_idx in seen:
            continue
        seen.add(chunk_idx)
        chunk = dict(chunks[chunk_idx])
        chunk["distance"] = float(distance)
        matches.append(chunk)
    return matches
