"""Topic router: match a student's requested topic against uploaded roadmap parts.

This intentionally stays deterministic and local. It uses part-level metadata
when available, and degrades to title/summary/transcript text for older fixed
segments. A future admin regeneration command can rebuild richer topic-based
roadmaps, but playback should already start from the best available part.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from backend.store import roadmap_parts_repo, roadmaps_repo, videos_repo
from config import ROADMAP_PART_MATCH_THRESHOLD

logger = logging.getLogger("parallea.topic_router")


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "for", "from", "how", "i", "in",
    "into", "is", "it", "me", "of", "on", "or", "please", "show", "some", "tell",
    "that", "the", "their", "to", "want", "what", "when", "with", "you", "your",
    "learn", "explain", "teach", "about", "do", "does", "can", "could", "would",
    "like", "need", "understand", "know", "topic",
}

_SYNONYMS = {
    "math": "mathematics",
    "maths": "mathematics",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "nn": "neural network",
    "svd": "singular value decomposition",
    "llm": "large language model",
    "llms": "large language model",
}


def _norm(text: Any) -> str:
    value = str(text or "").lower().replace(chr(8217), "'")
    for short, expanded in _SYNONYMS.items():
        value = re.sub(rf"\b{re.escape(short)}\b", expanded, value)
    value = re.sub(r"[^a-z0-9'+ ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _topic_query(text: str) -> str:
    norm = _norm(text)
    patterns = [
        r"^i\s+want\s+to\s+learn\s+",
        r"^i\s+would\s+like\s+to\s+learn\s+",
        r"^i\s+need\s+to\s+understand\s+",
        r"^teach\s+me\s+",
        r"^can\s+you\s+teach\s+me\s+",
        r"^can\s+you\s+explain\s+",
        r"^explain\s+",
        r"^tell\s+me\s+about\s+",
        r"^show\s+me\s+",
    ]
    for pattern in patterns:
        norm = re.sub(pattern, "", norm)
    return norm.strip() or _norm(text)


def _stem(token: str) -> str:
    token = token.lower()
    for suffix in ("ization", "ations", "ation", "ities", "ing", "ers", "ies", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            if suffix == "ies":
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token


def _tokens(text: Any) -> list[str]:
    return [
        _stem(token)
        for token in re.findall(r"[a-z0-9]+", _norm(text))
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _token_set(text: Any) -> set[str]:
    return set(_tokens(text))


def _overlap(query_tokens: set[str], text: Any) -> int:
    return len(query_tokens & _token_set(text))


def _contains_phrase(query: str, text: Any) -> bool:
    haystack = _norm(text)
    return bool(query and haystack and (query in haystack or haystack in query))


def _weighted_overlap(query_tokens: set[str], values: Any, weight: float) -> float:
    if isinstance(values, list):
        return sum(weight * _overlap(query_tokens, value) for value in values)
    return weight * _overlap(query_tokens, values)


def _part_search_text(part: dict[str, Any], roadmap: dict[str, Any], video: dict[str, Any] | None) -> str:
    fields: list[Any] = [
        part.get("title"),
        part.get("summary"),
        part.get("search_text"),
        part.get("keywords"),
        part.get("concepts"),
        part.get("equations"),
        part.get("examples"),
        part.get("suggested_visuals"),
        part.get("transcript_excerpt"),
        part.get("transcript_chunk"),
        roadmap.get("title"),
        roadmap.get("summary"),
        roadmap.get("topics"),
        (video or {}).get("title"),
        (video or {}).get("description"),
        (video or {}).get("detected_topics"),
    ]
    pieces: list[str] = []
    for value in fields:
        if isinstance(value, list):
            pieces.extend(str(item) for item in value if item)
        elif value:
            pieces.append(str(value))
    return " ".join(pieces)


def _score_part(
    *,
    query: str,
    query_tokens: set[str],
    part: dict[str, Any],
    roadmap: dict[str, Any],
    video: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    part_title = part.get("title") or ""
    if _contains_phrase(query, part_title):
        score += 22
        reasons.append("phrase match in part title")
    title_overlap = _overlap(query_tokens, part_title)
    if title_overlap:
        score += 8 * title_overlap
        reasons.append(f"{title_overlap} title token match(es)")

    for field_name, weight in (
        ("keywords", 9),
        ("concepts", 8),
        ("search_text", 6),
        ("summary", 5),
        ("examples", 4),
        ("equations", 4),
        ("suggested_visuals", 3),
        ("transcript_excerpt", 3),
        ("transcript_chunk", 2),
    ):
        value = part.get(field_name)
        overlap = _weighted_overlap(query_tokens, value, weight)
        if overlap:
            score += overlap
            reasons.append(f"{field_name} overlap")
        if field_name in {"keywords", "concepts", "search_text", "summary"} and _contains_phrase(query, value):
            score += 8
            reasons.append(f"phrase match in {field_name}")

    roadmap_topics_overlap = _weighted_overlap(query_tokens, roadmap.get("topics") or [], 3)
    if roadmap_topics_overlap:
        score += roadmap_topics_overlap
        reasons.append("roadmap topic overlap")
    if _contains_phrase(query, roadmap.get("title")):
        score += 5
        reasons.append("roadmap title phrase match")
    score += _weighted_overlap(query_tokens, roadmap.get("summary") or "", 1.5)

    if video:
        if _contains_phrase(query, video.get("title")):
            score += 4
            reasons.append("video title phrase match")
        score += _weighted_overlap(query_tokens, video.get("detected_topics") or [], 2)
        score += _weighted_overlap(query_tokens, video.get("description") or "", 1)

    return score, reasons


def _confidence(score: float, query_tokens: set[str]) -> float:
    # A direct title/concept hit on a short query should clear the threshold,
    # while weak roadmap-only overlap should not.
    target = max(32.0, len(query_tokens) * 14.0)
    return min(1.0, max(0.0, score / target))


def _ordered_parts_from(roadmap_id: str, start_part_id: str | None = None) -> list[dict[str, Any]]:
    parts = roadmap_parts_repo.where(roadmap_id=roadmap_id)
    parts.sort(key=lambda p: (p.get("order_index") if p.get("order_index") is not None else p.get("order") or 0, p.get("start_time") or 0))
    if not start_part_id:
        return parts
    index = next((idx for idx, item in enumerate(parts) if item.get("id") == start_part_id), -1)
    return parts[index:] if index >= 0 else parts


def _empty_response(student_topic_norm: str, *, confidence: float = 0.0, match_reason: str = "") -> dict[str, Any]:
    return {
        "topicExists": False,
        "confidence": round(confidence, 3),
        "mode": "persona_only",
        "matchedRoadmapId": None,
        "matchedVideoId": None,
        "matchedPartId": None,
        "matchedPartIds": [],
        "matchedTopic": None,
        "matchedPartTitle": None,
        "start_time": None,
        "end_time": None,
        "matchReason": match_reason or "no roadmap part cleared threshold",
        "confirmationRequired": True,
        "studentTopic": student_topic_norm,
    }


def match_student_topic_to_roadmaps(persona_id: str, student_topic: str) -> dict[str, Any]:
    """Return the part-level topic-router contract.

    The response keeps the older camelCase keys and adds explicit matched part
    metadata so session playback does not have to infer the first segment.
    """
    student_topic_norm = _topic_query(student_topic)
    query_tokens = _token_set(student_topic_norm)
    logger.info("[topic-match] student_query=%s persona_id=%s", student_topic_norm, persona_id)

    if not persona_id or not student_topic_norm or not query_tokens:
        return _empty_response(student_topic_norm, match_reason="empty query or persona")

    roadmaps = roadmaps_repo.where(persona_id=persona_id)
    if not roadmaps:
        return _empty_response(student_topic_norm, match_reason="persona has no roadmaps")

    best: dict[str, Any] | None = None
    for roadmap in roadmaps:
        video = videos_repo.get(roadmap.get("video_id") or "")
        parts = roadmap_parts_repo.where(roadmap_id=roadmap.get("id"))
        for part in parts:
            score, reasons = _score_part(
                query=student_topic_norm,
                query_tokens=query_tokens,
                part=part,
                roadmap=roadmap,
                video=video,
            )
            confidence = _confidence(score, query_tokens)
            candidate = {
                "score": score,
                "confidence": confidence,
                "roadmap": roadmap,
                "video": video,
                "part": part,
                "reasons": reasons,
            }
            if best is None or (score, confidence) > (best["score"], best["confidence"]):
                best = candidate

    if not best:
        return _empty_response(student_topic_norm, match_reason="roadmaps have no parts")

    part = best["part"]
    roadmap = best["roadmap"]
    video = best["video"] or {}
    confidence = round(float(best["confidence"]), 3)
    match_reason = "; ".join(best["reasons"][:5]) or "weak lexical overlap"
    logger.info(
        "[topic-match] matched_part_id=%s matched_part_title=%s confidence=%s start_time=%s end_time=%s reason=%s",
        part.get("id"),
        part.get("title"),
        confidence,
        part.get("start_time"),
        part.get("end_time"),
        match_reason,
    )

    if confidence < ROADMAP_PART_MATCH_THRESHOLD:
        return _empty_response(
            student_topic_norm,
            confidence=confidence,
            match_reason=f"best part below ROADMAP_PART_MATCH_THRESHOLD={ROADMAP_PART_MATCH_THRESHOLD}: {match_reason}",
        )

    consecutive = _ordered_parts_from(roadmap.get("id") or "", part.get("id"))
    matched_part_ids = [item.get("id") for item in consecutive if item.get("id")]
    search_text = _part_search_text(part, roadmap, video)
    return {
        "topicExists": True,
        "confidence": confidence,
        "mode": "video_context",
        "matchedRoadmapId": roadmap.get("id"),
        "matchedVideoId": video.get("id") or roadmap.get("video_id"),
        "matchedPartId": part.get("id"),
        "matchedPartIds": matched_part_ids or [part.get("id")],
        "matchedTopic": part.get("title") or roadmap.get("title"),
        "matchedPartTitle": part.get("title"),
        "start_time": part.get("start_time"),
        "end_time": part.get("end_time"),
        "matchReason": match_reason,
        "confirmationRequired": False,
        "studentTopic": student_topic_norm,
        "partSearchTextPreview": search_text[:400],
    }


def roadmap_part_regeneration_todo() -> str:
    """Manual TODO hook for future topic-based segmentation work.

    Existing roadmap parts may be fixed-length or sparse. Do not mutate them
    during student routing. A later admin command can regenerate richer fields
    such as keywords, search_text, transcript_excerpt, and next_part_id.
    """
    return "TODO: add an admin-only roadmap regeneration command for topic-based segmentation metadata."
