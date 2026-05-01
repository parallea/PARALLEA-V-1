"""Topic router: match a student's requested topic against a persona's
uploaded video roadmaps.

Algorithm (no embeddings; fast, deterministic, no API calls):
  - exact match against roadmap.topics list      → +60 per match
  - substring/word containment in roadmap.title  → +30
  - token overlap with roadmap.topics list       → +6 per token
  - token overlap with part.concepts             → +5 per token
  - token overlap with part.equations            → +3 per token
  - token overlap with part.examples             → +2 per token
  - student-topic in part.title (substring)      → +12

Best roadmap wins. Confidence = clamp(best_score / 60, 0..1).

If sentence-transformers + a cached video index exists (build_index from
data_indexer), we don't need it — the spec says use semantic match "if
embeddings already exist". They exist for the legacy chat path but they
encode chunk text rather than topic strings, so for topic routing the
keyword/concept overlap above already gives reliable results.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from backend.store import roadmap_parts_repo, roadmaps_repo

logger = logging.getLogger("parallea.topic_router")

# threshold above which we trust the match without asking the student for confirmation
VIDEO_CONTEXT_THRESHOLD = 0.40


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "for", "from", "how", "i", "in",
    "into", "is", "it", "me", "of", "on", "or", "please", "show", "some", "tell",
    "that", "the", "their", "to", "want", "what", "when", "with", "you", "your",
    "learn", "explain", "teach", "about", "do",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOPWORDS]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _topic_query(text: str) -> str:
    norm = _norm(text)
    patterns = [
        r"^i\s+want\s+to\s+learn\s+",
        r"^i\s+would\s+like\s+to\s+learn\s+",
        r"^teach\s+me\s+",
        r"^can\s+you\s+teach\s+me\s+",
        r"^explain\s+",
        r"^tell\s+me\s+about\s+",
    ]
    for pattern in patterns:
        norm = re.sub(pattern, "", norm)
    return norm.strip() or _norm(text)


def _token_overlap(a_tokens: set[str], b_text: str) -> int:
    return sum(1 for t in _tokens(b_text) if t in a_tokens)


def match_student_topic_to_roadmaps(persona_id: str, student_topic: str) -> dict[str, Any]:
    """Return the topic-router contract.

    {
      topicExists: bool,
      confidence: float,
      mode: 'video_context' | 'persona_only',
      matchedRoadmapId: str | None,
      matchedPartIds: list[str],
      matchedTopic: str | None,
      confirmationRequired: bool,
    }
    """
    student_topic_norm = _topic_query(student_topic)
    student_tokens_set = set(_tokens(student_topic_norm))

    if not persona_id or not student_topic_norm:
        return _empty_response(student_topic_norm)

    roadmaps = roadmaps_repo.where(persona_id=persona_id)
    if not roadmaps:
        return _empty_response(student_topic_norm)

    best_score = 0.0
    best_roadmap: dict[str, Any] | None = None
    best_parts: list[dict[str, Any]] = []
    best_matched_topic: str | None = None

    for roadmap in roadmaps:
        score = 0.0
        matched_topic = None

        # Exact + token match against the roadmap's topics list
        for topic in roadmap.get("topics") or []:
            tn = _norm(topic)
            if not tn:
                continue
            if tn == student_topic_norm or student_topic_norm in tn or tn in student_topic_norm:
                score += 60
                matched_topic = matched_topic or topic
                continue
            overlap = len(student_tokens_set & set(_tokens(tn)))
            if overlap:
                score += 6 * overlap
                matched_topic = matched_topic or topic

        # Title substring / token overlap
        title_norm = _norm(roadmap.get("title"))
        if title_norm and (student_topic_norm in title_norm or title_norm in student_topic_norm):
            score += 30
            matched_topic = matched_topic or roadmap.get("title")
        else:
            score += 4 * _token_overlap(student_tokens_set, title_norm)

        # Per-part scoring
        parts = roadmap_parts_repo.where(roadmap_id=roadmap["id"])
        scored_parts: list[tuple[float, dict[str, Any]]] = []
        for part in parts:
            part_score = 0.0
            part_title = _norm(part.get("title"))
            if part_title and student_topic_norm in part_title:
                part_score += 12
            part_score += 5 * sum(_token_overlap(student_tokens_set, c) for c in (part.get("concepts") or []))
            part_score += 3 * sum(_token_overlap(student_tokens_set, c) for c in (part.get("equations") or []))
            part_score += 2 * sum(_token_overlap(student_tokens_set, c) for c in (part.get("examples") or []))
            part_score += 1 * _token_overlap(student_tokens_set, part.get("summary") or "")
            if part_score > 0:
                scored_parts.append((part_score, part))
                score += part_score

        if score > best_score:
            best_score = score
            best_roadmap = roadmap
            scored_parts.sort(key=lambda kv: (-kv[0], kv[1].get("order") or 0))
            # Keep parts that scored, ordered by order of appearance in the roadmap.
            best_parts = (
                sorted([p for _, p in scored_parts], key=lambda p: p.get("order") or 0)
                if scored_parts
                else sorted(parts, key=lambda p: p.get("order") or 0)
            )
            best_matched_topic = matched_topic

    # Normalize confidence so 60+ scoring (a single direct topic hit) becomes ~1.0.
    confidence = min(1.0, best_score / 60.0)

    if best_roadmap and confidence >= VIDEO_CONTEXT_THRESHOLD:
        return {
            "topicExists": True,
            "confidence": round(confidence, 3),
            "mode": "video_context",
            "matchedRoadmapId": best_roadmap.get("id"),
            "matchedPartIds": [p.get("id") for p in best_parts][:6] or [parts[0].get("id") for parts in [roadmap_parts_repo.where(roadmap_id=best_roadmap["id"])] if parts][:1],
            "matchedTopic": best_matched_topic or (best_roadmap.get("topics") or [None])[0] or best_roadmap.get("title"),
            "confirmationRequired": False,
            "studentTopic": student_topic_norm,
        }

    return {
        "topicExists": False,
        "confidence": round(confidence, 3),
        "mode": "persona_only",
        "matchedRoadmapId": None,
        "matchedPartIds": [],
        "matchedTopic": None,
        "confirmationRequired": True,
        "studentTopic": student_topic_norm,
    }


def _empty_response(student_topic_norm: str) -> dict[str, Any]:
    return {
        "topicExists": False,
        "confidence": 0.0,
        "mode": "persona_only",
        "matchedRoadmapId": None,
        "matchedPartIds": [],
        "matchedTopic": None,
        "confirmationRequired": True,
        "studentTopic": student_topic_norm,
    }
