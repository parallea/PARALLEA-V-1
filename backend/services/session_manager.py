"""Student session state machine.

States (defined in `backend.store.models.SESSION_STATES`):
  greeting                         -> initial; assistant has just greeted
  awaiting_topic                   -> waiting for student to say what they want to learn
  topic_matching                   -> matching the requested topic against uploaded roadmaps
  playing_video_part               -> frontend is playing the original uploaded video part
  awaiting_part_feedback           -> assistant asked what the student did not understand
  clarifying_part_doubt            -> assistant is clarifying a doubt about the current part
  awaiting_clarification_feedback  -> assistant asked whether the clarification makes sense
  moving_to_next_part              -> selecting and teaching the next roadmap part
  persona_only_confirmation        -> topic isn't covered; ask student to confirm persona-only mode
  persona_only_teaching            -> assistant is teaching in persona-only mode (no roadmap)
  completed                        -> session ended

Public API:
  create_session(student, persona) -> session
  get_session_envelope(session_id) -> {session, persona, current_part, last_message, history}
  set_topic(session_id, topic)     -> updates session + returns topic-router result + video-part envelope
  mark_video_part_ended(session_id)-> asks the post-video understanding question
  send_message(session_id, content) -> processes student turn + returns assistant reply

The reply payload always has the same envelope:
  { session, message: { content, extra }, prompt_for: 'topic'|'video_part'|'part_feedback'|'confirmation'|'reply'|'next', visual?: {...} }
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from backend.services.answer_service import (
    CLARIFICATION_FOLLOWUP,
    MANIM_SCENE_CLASS_NAME,
    PART_UNDERSTANDING_QUESTION,
    build_fallback_manim_code,
    build_roadmap_part_context,
    generate_teaching_response_with_visuals,
    repair_manim_code_with_error,
)
from backend.services.topic_router import match_student_topic_to_roadmaps
from backend.visuals.manim_renderer import render_manim_payload_async
from backend.store import (
    messages_repo,
    missing_topics_repo,
    personas_repo,
    roadmap_parts_repo,
    roadmaps_repo,
    sessions_repo,
    users_repo,
    videos_repo,
)
from backend.store.models import (
    MissingTopicRequest,
    StudentMessage,
    StudentSession,
    utcnow,
)
from manim_renderer import direct_manim_validation_error

logger = logging.getLogger("parallea.session")


_AFFIRMATIVE = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "sounds good", "go ahead", "continue", "next", "go", "let's go", "lets go", "please do", "do it", "alright"}
_NEGATIVE = {"no", "n", "nope", "stop", "cancel", "not now", "nah", "later"}
_QUESTION_TRIGGERS = {"question", "ask", "doubt", "wait", "actually"}
_UNDERSTOOD_PATTERNS = (
    "i understand",
    "understood",
    "got it",
    "i got it",
    "makes sense",
    "that makes sense",
    "all clear",
    "clear",
    "continue",
    "next",
    "move on",
    "go ahead",
    "go on",
)
_NO_DOUBT_PATTERNS = (
    "no",
    "no doubt",
    "no doubts",
    "no question",
    "no questions",
    "nothing",
    "nothing else",
    "all good",
)
_DOUBT_PATTERNS = (
    "didn't understand",
    "did not understand",
    "dont understand",
    "don't understand",
    "didn't get",
    "did not get",
    "dont get",
    "don't get",
    "confused",
    "explain again",
    "repeat",
    "again",
    "slow down",
    "visually",
    "visual",
    "show me",
    "what does",
    "why",
    "how",
)
_STATE_ALIASES = {
    "confirming_persona_only": "persona_only_confirmation",
    "teaching_video_part": "playing_video_part",
    "teaching_roadmap_part": "playing_video_part",
    "waiting_for_part_confirmation": "awaiting_part_feedback",
    "answering_question": "clarifying_part_doubt",
    "teaching_persona_only": "persona_only_teaching",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _greeting_text(student_name: str, persona: dict[str, Any]) -> str:
    teacher = persona.get("teacher_name") or "your teacher"
    profession = persona.get("profession") or "subject expert"
    topics = list(persona.get("detected_topics") or [])
    topics_phrase = ""
    if topics:
        sample = topics[:5]
        topics_phrase = f" I know topics like {', '.join(sample[:-1]) + (' and ' + sample[-1] if len(sample) > 1 else sample[0])}."
    name_part = student_name.strip() or "there"
    return f"Hey {name_part}, I am {teacher}. I am a {profession}.{topics_phrase} What do you want to learn today?"


def _confirm_persona_only_text(topic: str) -> str:
    topic_show = topic.strip() or "that topic"
    return (
        f"I haven't uploaded a video on {topic_show} yet, but I can still explain it in my teaching style. "
        f"Want me to go ahead?"
    )


def _record_message(session_id: str, role: str, content: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    message = messages_repo.create(
        StudentMessage(session_id=session_id, role=role, content=content, extra=extra or {})
    )
    _sync_session_memory_after_message(session_id, role=role, content=content, extra=extra or {})
    return message


def _history_excerpt(session_id: str, limit: int = 6) -> str:
    msgs = messages_repo.where(session_id=session_id)
    msgs.sort(key=lambda m: m.get("created_at") or "")
    msgs = msgs[-limit:]
    return "\n".join(f"{m.get('role','?').upper()}: {m.get('content','')}" for m in msgs)


_MEMORY_DEFAULTS: dict[str, Any] = {
    "current_topic": "",
    "current_step": "",
    "last_user_message": "",
    "last_assistant_answer": "",
    "last_visual_plan": [],
    "student_understanding_summary": "",
    "unresolved_student_question": "",
    "next_teaching_goal": "",
    "recent_turns": [],
}


def _recent_turns(session_id: str, limit: int = 6) -> list[dict[str, Any]]:
    msgs = [m for m in messages_repo.where(session_id=session_id) if m.get("role") in {"student", "assistant"}]
    msgs.sort(key=lambda m: m.get("created_at") or "")
    return [
        {
            "role": m.get("role"),
            "content": m.get("content") or "",
            "created_at": m.get("created_at"),
        }
        for m in msgs[-limit:]
    ]


def _memory_current_step(session: dict[str, Any]) -> str:
    part = _current_part(session)
    if part:
        title = part.get("title") or part.get("part_id") or part.get("id") or ""
        order = part.get("order")
        return f"part {order}: {title}" if order not in {None, ""} else str(title)
    index = session.get("current_part_index")
    if index not in {None, ""}:
        return f"step {index}"
    memory = session.get("memory") if isinstance(session.get("memory"), dict) else {}
    return str(memory.get("current_step") or "")


def _coerce_memory(session: dict[str, Any] | None) -> dict[str, Any]:
    memory = dict(_MEMORY_DEFAULTS)
    existing = (session or {}).get("memory")
    if isinstance(existing, dict):
        for key in _MEMORY_DEFAULTS:
            if key in existing and existing[key] is not None:
                memory[key] = existing[key]
    return memory


def _sync_session_memory_after_message(
    session_id: str,
    *,
    role: str | None = None,
    content: str = "",
    extra: dict[str, Any] | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    if not session:
        return {}
    memory = _coerce_memory(session)
    memory["current_topic"] = session.get("selected_topic") or memory.get("current_topic") or ""
    memory["current_step"] = _memory_current_step(session)
    if role == "student":
        memory["last_user_message"] = content or ""
        if content:
            memory["unresolved_student_question"] = content
    elif role == "assistant":
        memory["last_assistant_answer"] = content or ""
        if content and not memory.get("student_understanding_summary"):
            memory["student_understanding_summary"] = "Awaiting the student's response to the latest explanation."
    extra = extra if isinstance(extra, dict) else {}
    state_update = extra.get("teachingStateUpdate") or extra.get("teaching_state_update")
    if isinstance(state_update, dict):
        for key in (
            "current_topic",
            "current_step",
            "last_user_message",
            "last_assistant_answer",
            "last_visual_plan",
            "student_understanding_summary",
            "unresolved_student_question",
            "next_teaching_goal",
        ):
            value = state_update.get(key)
            if key in state_update and value is not None and value != "":
                memory[key] = value
    visual_plan = None
    if isinstance(extra.get("visual"), dict):
        visual_plan = extra["visual"].get("timestamps") or extra["visual"].get("syncPlan") or extra["visual"].get("visualPlanWithTimestamps")
    if visual_plan is None and isinstance(extra.get("syncPlan"), dict):
        visual_plan = extra["syncPlan"].get("segments")
    if visual_plan:
        memory["last_visual_plan"] = visual_plan
    teaching_control = extra.get("teachingControl") if isinstance(extra.get("teachingControl"), dict) else {}
    ask = extra.get("askFollowUp") or teaching_control.get("askFollowUp")
    if ask:
        memory["next_teaching_goal"] = str(ask)
    if updates:
        for key, value in updates.items():
            if key in _MEMORY_DEFAULTS and value is not None:
                memory[key] = value
    memory["recent_turns"] = _recent_turns(session_id, limit=6)
    memory["updated_at"] = utcnow()
    sessions_repo.update(session_id, {"memory": memory})
    logger.info(
        "session memory updated session=%s role=%s current_topic=%s current_step=%s previous_assistant_included=%s recent_turns=%s",
        session_id,
        role or "-",
        memory.get("current_topic"),
        memory.get("current_step"),
        bool(memory.get("last_assistant_answer")),
        len(memory.get("recent_turns") or []),
    )
    return memory


def _session_memory_for_prompt(session_id: str) -> dict[str, Any]:
    return _sync_session_memory_after_message(session_id)


def _persona_for_session(session: dict[str, Any]) -> dict[str, Any] | None:
    return personas_repo.get(session.get("persona_id") or "")


def _student_for_session(session: dict[str, Any]) -> dict[str, Any] | None:
    return users_repo.get(session.get("student_id") or "")


def _current_part(session: dict[str, Any]) -> dict[str, Any] | None:
    pid = session.get("current_part_id")
    if not pid:
        return None
    return roadmap_parts_repo.get(pid)


def _current_roadmap(session: dict[str, Any]) -> dict[str, Any] | None:
    rid = session.get("current_roadmap_id")
    if not rid:
        return None
    return roadmaps_repo.get(rid)


def _current_video(session: dict[str, Any]) -> dict[str, Any] | None:
    roadmap = _current_roadmap(session)
    if not roadmap:
        return None
    return videos_repo.get(roadmap.get("video_id") or "")


def _ordered_parts(roadmap_id: str) -> list[dict[str, Any]]:
    parts = roadmap_parts_repo.where(roadmap_id=roadmap_id)
    parts.sort(key=lambda p: p.get("order") or 0)
    return parts


def _canonical_state(state: str | None) -> str:
    return _STATE_ALIASES.get(state or "", state or "")


def _intent_from_text(text: str) -> str:
    norm = (text or "").strip().lower()
    norm = re.sub(r"[^a-z' ]+", "", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return "empty"
    if norm in _NEGATIVE:
        return "no"
    if norm in _AFFIRMATIVE:
        return "yes"
    # Single-word affirmatives buried in a phrase
    tokens = set(norm.split())
    if tokens & _NEGATIVE:
        return "no"
    if tokens & _QUESTION_TRIGGERS:
        return "question"
    if tokens & _AFFIRMATIVE:
        return "yes"
    return "free_text"


def _norm_feedback_text(text: str) -> str:
    norm = (text or "").strip().lower()
    norm = norm.replace(chr(8217), "'")
    norm = re.sub(r"[^a-z0-9' ]+", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def _clean_topic_from_transcript(text: str) -> str:
    raw = (text or "").strip()
    cleaned = re.sub(r"\s+", " ", raw).strip(" .?!,;:")
    lowered = cleaned.lower()
    patterns = [
        r"^(?:i\s+)?(?:want|wanna|would like|need)\s+to\s+(?:learn|study|understand|know)\s+(?:about\s+)?(.+)$",
        r"^(?:can|could)\s+you\s+(?:teach|explain|show)\s+(?:me\s+)?(?:about\s+)?(.+)$",
        r"^(?:teach|explain|show)\s+(?:me\s+)?(?:about\s+)?(.+)$",
        r"^(?:let'?s|lets)\s+(?:learn|study)\s+(?:about\s+)?(.+)$",
        r"^(?:i\s+am\s+interested\s+in|i'?m\s+interested\s+in)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, lowered, flags=re.I)
        if match:
            candidate = cleaned[match.start(1):match.end(1)].strip(" .?!,;:")
            candidate = re.sub(r"\b(?:today|please|sir|ma'?am|mam)$", "", candidate, flags=re.I).strip(" .?!,;:")
            return candidate or cleaned
    return cleaned


def _part_feedback_intent(text: str) -> str:
    """Student is answering: "Is there anything in this part you didn't understand?"

    For this question, "yes" means there is a doubt, while "no"/"got it"/"continue"
    means advance.
    """
    norm = _norm_feedback_text(text)
    if not norm:
        return "empty"
    if norm in {"yes", "y", "yeah", "yep"}:
        return "doubt"
    if norm in _NO_DOUBT_PATTERNS or any(phrase in norm for phrase in _UNDERSTOOD_PATTERNS):
        return "continue"
    if any(phrase in norm for phrase in _DOUBT_PATTERNS):
        return "doubt"
    return "doubt"


def _clarification_feedback_intent(text: str) -> str:
    """Student is answering: "Does that make sense now...?"

    Here "yes"/"continue" advances; "no"/free-text asks for another clarification.
    """
    norm = _norm_feedback_text(text)
    if not norm:
        return "empty"
    if norm in {"yes", "y", "yeah", "yep", "ok", "okay", "sure"}:
        return "continue"
    if any(phrase in norm for phrase in _UNDERSTOOD_PATTERNS):
        return "continue"
    if norm in {"no", "n", "nope"} or any(phrase in norm for phrase in _DOUBT_PATTERNS):
        return "doubt"
    return "doubt"


def _append_followup(speech: str, followup: str) -> str:
    base = (speech or "").strip()
    if not base:
        return followup
    if followup.lower().rstrip(" ?.!") in base.lower().rstrip(" ?.!"):
        return base
    return f"{base}\n\n{followup}"


def _envelope(session: dict[str, Any], message: dict[str, Any] | None, prompt_for: str, visual: dict[str, Any] | None = None) -> dict[str, Any]:
    persona = _persona_for_session(session) or {}
    part = _current_part(session)
    video = _current_video(session)
    return {
        "session": _public_session(session),
        "persona": _public_persona(persona),
        "currentPart": _public_part(part) if part else None,
        "currentVideo": _public_video(video) if video else None,
        "message": _public_message(message) if message else None,
        "promptFor": prompt_for,
        "visual": visual,
    }


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session.get("id"),
        "persona_id": session.get("persona_id"),
        "state": _canonical_state(session.get("state")),
        "selected_topic": session.get("selected_topic"),
        "mode": session.get("mode"),
        "current_roadmap_id": session.get("current_roadmap_id"),
        "current_part_id": session.get("current_part_id"),
        "current_part_index": session.get("current_part_index"),
        "matched_part_ids": session.get("matched_part_ids") or [],
        "confidence": session.get("confidence") or 0.0,
        "memory": _coerce_memory(session),
        "updated_at": session.get("updated_at"),
    }


def _public_persona(persona: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": persona.get("id"),
        "teacher_name": persona.get("teacher_name"),
        "profession": persona.get("profession"),
        "style_summary": persona.get("style_summary"),
        "avatar_image_url": persona.get("avatar_image_url"),
        "avatar_preset_id": persona.get("avatar_preset_id"),
        "voice_id": persona.get("voice_id"),
        "detected_topics": persona.get("detected_topics") or [],
    }


def _public_part(part: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": part.get("id"),
        "part_id": part.get("part_id"),
        "order": part.get("order"),
        "title": part.get("title"),
        "summary": part.get("summary"),
        "transcript_chunk": part.get("transcript_chunk") or "",
        "start_time": part.get("start_time"),
        "end_time": part.get("end_time"),
        "concepts": part.get("concepts") or [],
        "equations": part.get("equations") or [],
        "examples": part.get("examples") or [],
        "suggested_visuals": part.get("suggested_visuals") or [],
    }


def _public_video(video: dict[str, Any]) -> dict[str, Any]:
    video_id = video.get("id") or ""
    return {
        "id": video_id,
        "title": video.get("title"),
        "duration": video.get("duration"),
        "thumbnail_url": video.get("thumbnail_url"),
        "stream_url": f"/api/student/videos/{video_id}/stream" if video_id else None,
    }


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message.get("id"),
        "role": message.get("role"),
        "content": message.get("content"),
        "extra": message.get("extra") or {},
        "created_at": message.get("created_at"),
    }


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------


def create_session(*, student: dict[str, Any], persona: dict[str, Any]) -> dict[str, Any]:
    session = sessions_repo.create(
        StudentSession(
            student_id=student["id"],
            persona_id=persona["id"],
            state="greeting",
        )
    )
    greeting = _greeting_text(student.get("name") or "", persona)
    msg = _record_message(session["id"], "assistant", greeting, {"kind": "greeting"})
    sessions_repo.update(session["id"], {"state": "awaiting_topic"})
    session = sessions_repo.get(session["id"])
    logger.info(
        "session created selected_persona_id=%s available_topics=%s current_session_state=%s",
        persona.get("id"),
        persona.get("detected_topics") or [],
        session.get("state"),
    )
    return _envelope(session, msg, prompt_for="topic")


def get_session_envelope(session_id: str) -> Optional[dict[str, Any]]:
    session = sessions_repo.get(session_id)
    if not session:
        return None
    msgs = messages_repo.where(session_id=session_id)
    msgs.sort(key=lambda m: m.get("created_at") or "")
    history = [_public_message(m) for m in msgs]
    last = msgs[-1] if msgs else None
    env = _envelope(session, last, prompt_for=_prompt_for_state(session))
    env["history"] = history
    return env


def _prompt_for_state(session: dict[str, Any]) -> str:
    state = _canonical_state(session.get("state"))
    if state == "awaiting_topic":
        return "topic"
    if state == "persona_only_confirmation":
        return "confirmation"
    if state == "awaiting_part_feedback":
        return "part_feedback"
    if state == "awaiting_clarification_feedback":
        return "clarification_feedback"
    if state == "playing_video_part":
        return "video_part"
    if state == "moving_to_next_part":
        return "next"
    if state == "completed":
        return "completed"
    return "reply"


# ---------------------------------------------------------------------------
# topic routing
# ---------------------------------------------------------------------------


async def set_topic(session_id: str, topic: str, *, record_student: bool = True) -> Optional[dict[str, Any]]:
    session = sessions_repo.get(session_id)
    if not session:
        return None
    persona = _persona_for_session(session)
    if not persona:
        return None
    raw_topic = topic.strip()
    topic = _clean_topic_from_transcript(raw_topic)
    if record_student:
        _record_message(session_id, "student", raw_topic, {"kind": "topic", "cleaned_topic": topic})
    sessions_repo.update(session_id, {"state": "topic_matching"})
    logger.info(
        "student requested topic selected_persona_id=%s available_topics=%s raw_transcript=%s cleaned_topic=%s current_session_state=%s",
        persona.get("id"),
        persona.get("detected_topics") or [],
        raw_topic,
        topic,
        "topic_matching",
    )
    routing = match_student_topic_to_roadmaps(persona["id"], topic)
    sessions_repo.update(
        session_id,
        {
            "selected_topic": topic,
            "mode": routing["mode"],
            "matched_part_ids": routing.get("matchedPartIds") or [],
            "current_roadmap_id": routing.get("matchedRoadmapId"),
            "confidence": routing.get("confidence", 0.0),
        },
    )
    _sync_session_memory_after_message(
        session_id,
        updates={
            "current_topic": topic,
            "current_step": "topic matching",
            "last_user_message": raw_topic,
            "next_teaching_goal": "Match the student topic to the uploaded teacher roadmap or persona-only teaching.",
        },
    )
    logger.info(
        "topic match result selected_persona_id=%s matched_roadmap_id=%s matched_confidence=%s topic_exists=%s",
        persona.get("id"),
        routing.get("matchedRoadmapId"),
        routing.get("confidence", 0.0),
        routing.get("topicExists"),
    )
    if routing["topicExists"]:
        return _start_uploaded_video_part(session_id, routing)
    # persona_only path: record the missing topic and ask confirmation.
    missing_topics_repo.create(
        MissingTopicRequest(
            student_id=session.get("student_id") or "",
            persona_id=persona["id"],
            topic=topic,
        )
    )
    sessions_repo.update(session_id, {"state": "persona_only_confirmation", "current_part_id": None})
    confirm_text = _confirm_persona_only_text(topic)
    msg = _record_message(session_id, "assistant", confirm_text, {"kind": "confirm", "routing": routing})
    logger.info("fallback to persona-only mode session=%s topic=%s current_session_state=%s", session_id, topic, "persona_only_confirmation")
    return _envelope(sessions_repo.get(session_id), msg, prompt_for="confirmation")


def _start_video_context_teaching(session_id: str, routing: dict[str, Any]) -> dict[str, Any]:
    return _start_uploaded_video_part(session_id, routing)
    session = sessions_repo.get(session_id)
    parts_ids = routing.get("matchedPartIds") or []
    first_part_id = parts_ids[0] if parts_ids else None
    if not first_part_id:
        # Roadmap exists but no parts matched specifically — fall back to first part of the roadmap.
        ordered = _ordered_parts(routing["matchedRoadmapId"])
        first_part_id = ordered[0]["id"] if ordered else None
    if not first_part_id:
        sessions_repo.update(session_id, {"state": "persona_only_confirmation", "mode": "persona_only"})
        msg = _record_message(session_id, "assistant", _confirm_persona_only_text(routing.get("studentTopic", "")), {"kind": "confirm", "fallback": "no_parts"})
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="confirmation")

    first_part = roadmap_parts_repo.get(first_part_id)
    sessions_repo.update(
        session_id,
        {
            "state": "playing_video_part",
            "current_part_id": first_part_id,
            "current_part_index": (first_part or {}).get("order") or 0,
        },
    )
    concepts = ", ".join((first_part or {}).get("concepts") or [])
    intro = (
        f"Got it. Let's start with: {(first_part or {}).get('title') or 'the first part'}. "
        f"{(first_part or {}).get('summary') or ''}"
    ).strip()
    if concepts:
        intro = f"{intro} The key ideas here are {concepts}."
    msg = _record_message(session_id, "assistant", intro, {"kind": "teach_part_intro", "routing": routing, "part_id": first_part_id})
    sessions_repo.update(session_id, {"state": "awaiting_part_feedback"})
    follow_up = _record_message(
        session_id,
        "assistant",
        f"{intro}\n\nDo you have any questions about this part before we move forward?",
        {"kind": "ask_question_check", "part_id": first_part_id},
    )
    return _envelope(sessions_repo.get(session_id), follow_up, prompt_for="next")


def _start_uploaded_video_part(session_id: str, routing: dict[str, Any]) -> dict[str, Any]:
    roadmap_id = routing.get("matchedRoadmapId")
    ordered = _ordered_parts(roadmap_id) if roadmap_id else []
    first_part_id = ordered[0]["id"] if ordered else None
    if not first_part_id:
        sessions_repo.update(session_id, {"state": "persona_only_confirmation", "mode": "persona_only"})
        msg = _record_message(
            session_id,
            "assistant",
            _confirm_persona_only_text(routing.get("studentTopic", "")),
            {"kind": "confirm", "fallback": "no_parts"},
        )
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="confirmation")

    first_part = roadmap_parts_repo.get(first_part_id)
    sessions_repo.update(
        session_id,
        {
            "state": "playing_video_part",
            "current_part_id": first_part_id,
            "current_part_index": (first_part or {}).get("order") or 0,
            "matched_part_ids": [part["id"] for part in ordered],
        },
    )
    _sync_session_memory_after_message(
        session_id,
        updates={
            "current_step": f"part {(first_part or {}).get('order')}: {(first_part or {}).get('title') or first_part_id}",
            "next_teaching_goal": "Play the original uploaded teacher video part before asking for questions.",
        },
    )
    logger.info(
        "current roadmap part selected session=%s matched_roadmap_id=%s current_roadmap_part_id=%s current_session_state=%s",
        session_id,
        roadmap_id,
        first_part_id,
        "playing_video_part",
    )
    return _video_part_ready_envelope(session_id, routing=routing)


def _video_part_ready_envelope(session_id: str, *, routing: dict[str, Any] | None = None) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    roadmap = _current_roadmap(session or {})
    part = _current_part(session or {})
    video = _current_video(session or {})
    if not session or not roadmap or not part or not video:
        sessions_repo.update(session_id, {"state": "persona_only_confirmation", "mode": "persona_only"})
        msg = _record_message(
            session_id,
            "assistant",
            _confirm_persona_only_text((session or {}).get("selected_topic") or ""),
            {"kind": "confirm", "fallback": "missing_video_segment"},
        )
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="confirmation")

    start = part.get("start_time")
    end = part.get("end_time")
    logger.info(
        "original video segment ready session=%s matched_roadmap_id=%s current_roadmap_part_id=%s video_id=%s start=%s end=%s current_session_state=%s",
        session_id,
        roadmap.get("id"),
        part.get("id"),
        video.get("id"),
        start,
        end,
        "playing_video_part",
    )
    msg = _record_message(
        session_id,
        "system",
        "Original teacher video part is ready.",
        {
            "kind": "video_part_ready",
            "routing": routing or {},
            "roadmap_id": roadmap.get("id"),
            "part_id": part.get("id"),
            "video_id": video.get("id"),
            "segment": {"start": start, "end": end},
        },
    )
    return _envelope(sessions_repo.get(session_id), msg, prompt_for="video_part")


async def teach_current_roadmap_part(session_id: str, routing: dict[str, Any] | None = None) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    roadmap = _current_roadmap(session or {})
    part = _current_part(session or {})
    if not session or not roadmap or not part:
        sessions_repo.update(session_id, {"state": "completed"})
        msg = _record_message(
            session_id,
            "assistant",
            "I could not find the current roadmap part. What would you like to learn instead?",
            {"kind": "missing_part"},
        )
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")
    sessions_repo.update(session_id, {"state": "playing_video_part"})
    return _video_part_ready_envelope(session_id, routing=routing)


def mark_video_part_ended(session_id: str) -> Optional[dict[str, Any]]:
    session = sessions_repo.get(session_id)
    if not session:
        return None
    part = _current_part(session)
    roadmap = _current_roadmap(session)
    if not part or not roadmap:
        sessions_repo.update(session_id, {"state": "awaiting_topic"})
        msg = _record_message(
            session_id,
            "assistant",
            "I lost the current video part. Tell me the topic again and I will match it to the uploaded lesson.",
            {"kind": "missing_current_part"},
        )
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")

    sessions_repo.update(session_id, {"state": "awaiting_part_feedback"})
    msg = _record_message(
        session_id,
        "assistant",
        PART_UNDERSTANDING_QUESTION,
        {
            "kind": "part_end_followup",
            "source": "original_video_part",
            "roadmap_id": roadmap.get("id"),
            "part_id": part.get("id"),
            "askFollowUp": PART_UNDERSTANDING_QUESTION,
        },
    )
    logger.info(
        "part-end follow-up triggered session=%s matched_roadmap_id=%s current_roadmap_part_id=%s current_session_state=%s",
        session_id,
        roadmap.get("id"),
        part.get("id"),
        "awaiting_part_feedback",
    )
    return _envelope(sessions_repo.get(session_id), msg, prompt_for="part_feedback")


# ---------------------------------------------------------------------------
# main dispatch on student message
# ---------------------------------------------------------------------------


async def send_message(session_id: str, content: str) -> Optional[dict[str, Any]]:
    session = sessions_repo.get(session_id)
    if not session:
        return None
    persona = _persona_for_session(session)
    student = _student_for_session(session)
    if not persona or not student:
        return None
    student_text = (content or "").strip()
    if not student_text:
        return _envelope(session, None, prompt_for=_prompt_for_state(session))
    _record_message(session_id, "student", student_text)

    state = _canonical_state(session.get("state"))
    if state != session.get("state"):
        session = sessions_repo.update(session_id, {"state": state}) or session
    intent = _intent_from_text(student_text)

    if state == "greeting" or state == "awaiting_topic":
        # Treat the message as the topic.
        return await set_topic(session_id, student_text, record_student=False)

    if state == "playing_video_part":
        logger.info("student message ignored during original video playback session=%s text=%s", session_id, student_text[:120])
        return _video_part_ready_envelope(session_id)

    if state == "persona_only_confirmation":
        if intent == "yes":
            return await _start_persona_only_teaching(session_id)
        if intent == "no":
            sessions_repo.update(session_id, {"state": "awaiting_topic", "mode": None, "selected_topic": None})
            msg = _record_message(session_id, "assistant", "No problem. What would you like to learn instead?", {"kind": "reset_topic"})
            return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")
        # ambiguous: re-ask
        msg = _record_message(session_id, "assistant", "Just to confirm: should I go ahead and teach this in my style? (yes/no)", {"kind": "confirm_reask"})
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="confirmation")

    if state == "awaiting_part_feedback":
        feedback_intent = _part_feedback_intent(student_text)
        logger.info("part feedback received session=%s intent=%s text=%s", session_id, feedback_intent, student_text[:120])
        if feedback_intent == "continue":
            return await _advance_to_next_part(session_id)
        return await _clarify_current_roadmap_part(session_id, student_text)

    if state == "awaiting_clarification_feedback":
        feedback_intent = _clarification_feedback_intent(student_text)
        logger.info("clarification feedback received session=%s intent=%s text=%s", session_id, feedback_intent, student_text[:120])
        if feedback_intent == "continue":
            return await _advance_to_next_part(session_id)
        return await _clarify_current_roadmap_part(session_id, student_text)

    if state == "clarifying_part_doubt":
        return await _clarify_current_roadmap_part(session_id, student_text)

    if state == "persona_only_teaching":
        return await _answer_persona_only(session_id, student_text)

    if state == "completed":
        # Allow restart with a new topic.
        sessions_repo.update(session_id, {"state": "awaiting_topic"})
        return await set_topic(session_id, student_text, record_student=False)

    # Fallback: route as a question.
    return await _clarify_current_roadmap_part(session_id, student_text)


async def _advance_to_next_part(session_id: str) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    roadmap_id = session.get("current_roadmap_id")
    if not roadmap_id:
        sessions_repo.update(session_id, {"state": "completed"})
        msg = _record_message(session_id, "assistant", "Looks like we wrapped that one up. Want to learn something else?", {"kind": "completed"})
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")
    parts = _ordered_parts(roadmap_id)
    current_id = session.get("current_part_id")
    idx = next((i for i, p in enumerate(parts) if p["id"] == current_id), -1)
    next_idx = idx + 1
    if next_idx >= len(parts):
        sessions_repo.update(session_id, {"state": "completed", "current_part_id": None})
        msg = _record_message(
            session_id,
            "assistant",
            "That's the end of this roadmap. Want to dive into something else, or wrap up here?",
            {"kind": "completed_roadmap"},
        )
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")
    next_part = parts[next_idx]
    sessions_repo.update(
        session_id,
        {
            "state": "playing_video_part",
            "current_part_id": next_part["id"],
            "current_part_index": next_part.get("order") or next_idx,
        },
    )
    _sync_session_memory_after_message(
        session_id,
        updates={
            "current_step": f"part {next_part.get('order')}: {next_part.get('title') or next_part.get('id')}",
            "next_teaching_goal": "Continue to the next uploaded teacher video part.",
            "unresolved_student_question": "",
        },
    )
    logger.info(
        "next part selected session=%s matched_roadmap_id=%s current_roadmap_part_id=%s next_index=%s",
        session_id,
        roadmap_id,
        next_part.get("id"),
        next_idx,
    )
    return _video_part_ready_envelope(session_id)


async def _clarify_current_roadmap_part(session_id: str, student_text: str) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    persona = _persona_for_session(session) or {}
    student = _student_for_session(session) or {}
    roadmap = _current_roadmap(session or {})
    part = _current_part(session)
    if not part or not roadmap:
        msg = _record_message(
            session_id,
            "assistant",
            "I do not have an active roadmap part for that yet. Tell me the topic again and I will match it to the uploaded roadmap.",
            {"kind": "missing_current_part"},
        )
        sessions_repo.update(session_id, {"state": "awaiting_topic"})
        return _envelope(sessions_repo.get(session_id), msg, prompt_for="topic")

    sessions_repo.update(session_id, {"state": "clarifying_part_doubt"})
    memory = _session_memory_for_prompt(session_id)
    previous_answer = str(memory.get("last_assistant_answer") or "")
    logger.info(
        "building clarification prompt session=%s model_context current_topic=%s current_step=%s recent_turns=%s previous_assistant_included=%s video_context_included=%s",
        session_id,
        memory.get("current_topic") or session.get("selected_topic") or "",
        memory.get("current_step") or "",
        len(memory.get("recent_turns") or []),
        bool(previous_answer),
        bool(part.get("transcript_chunk") or part.get("summary")),
    )
    payload = await generate_teaching_response_with_visuals(
        mode="video_context_clarification",
        persona_prompt=persona.get("active_persona_prompt") or "",
        student_name=student.get("name") or "",
        teacher_name=persona.get("teacher_name") or "",
        teacher_profession=persona.get("profession") or "",
        topic=session.get("selected_topic") or "",
        student_query=student_text,
        current_roadmap_part=part,
        part_context=build_roadmap_part_context(roadmap, part),
        available_visual_mode="manim",
        session_memory=memory,
        previous_assistant_answer=previous_answer,
    )
    speech_segments = ((payload.get("speech") or {}).get("segments") or (payload.get("speech") or {}).get("timestamps") or [])
    visual_segments = ((payload.get("visual") or {}).get("segments") or (payload.get("visual") or {}).get("timestamps") or [])
    logger.info(
        "clarification timestamps returned session=%s speech_timestamps=%s manim_timestamps=%s",
        session_id,
        len(speech_segments),
        len(visual_segments),
    )
    visual = await _render_teaching_visual(
        session_id,
        payload,
        student_query=student_text,
        title=part.get("title") or "Clarification",
        subtitle=((part.get("suggested_visuals") or [part.get("summary") or "Visual clarification"])[0]),
        cache_segment_id=f"{part.get('id') or 'part'}_clarification",
    )
    speech_text = ((payload.get("speech") or {}).get("text") or "").strip()
    ask_followup = ((payload.get("teachingControl") or {}).get("askFollowUp") or payload.get("askFollowUp") or CLARIFICATION_FOLLOWUP)
    content = _append_followup(speech_text, ask_followup)
    msg = _record_message(
        session_id,
        "assistant",
        content,
        {
            "kind": "clarify_roadmap_part",
            "source": "roadmap_part",
            "roadmap_id": roadmap.get("id"),
            "part_id": part.get("id"),
            "student_doubt": student_text,
            "speech": payload.get("speech") or {},
            "syncPlan": payload.get("syncPlan") or {},
            "teachingStateUpdate": payload.get("teachingStateUpdate") or {},
            "visualPlanWithTimestamps": payload.get("visualPlanWithTimestamps") or [],
            "askFollowUp": ask_followup,
            "teachingControl": payload.get("teachingControl") or {},
            "debug": payload.get("debug") or {},
            "visual": visual,
        },
    )
    sessions_repo.update(session_id, {"state": "awaiting_clarification_feedback"})
    logger.info(
        "clarification complete session=%s current_roadmap_part_id=%s visual_render_status=%s sync_plan_used=%s current_session_state=%s",
        session_id,
        part.get("id"),
        (visual or {}).get("renderStatus"),
        bool((payload.get("syncPlan") or {}).get("segments")),
        "awaiting_clarification_feedback",
    )
    return _envelope(sessions_repo.get(session_id), msg, prompt_for="clarification_feedback", visual=visual)


def _extract_manim_error_log(exc: Exception) -> str:
    text = str(exc)
    marker = "stderr_log="
    if marker not in text:
        return text
    path_text = text.split(marker, 1)[1].strip().strip('"')
    try:
        path = Path(path_text)
        if path.exists():
            return f"{text}\n\nSTDERR_LOG_BEGIN\n{path.read_text(encoding='utf-8', errors='replace')[-5000:]}\nSTDERR_LOG_END"
    except Exception:
        return text
    return text


async def _render_teaching_visual(
    session_id: str,
    payload: dict[str, Any],
    *,
    student_query: str = "",
    title: str = "Visual explanation",
    subtitle: str = "Visual clarification",
    cache_segment_id: str = "teaching_visual",
) -> dict[str, Any] | None:
    visual_payload = payload.get("visual") if isinstance(payload.get("visual"), dict) else {}
    sync_plan = payload.get("syncPlan") or {}
    timestamps = visual_payload.get("timestamps") or visual_payload.get("segments") or []
    if not visual_payload.get("visualNeeded", True):
        logger.info("[teaching-visual] visualNeeded=false session=%s title=%s", session_id, title)
        return {
            "type": "manim",
            "visualType": "manim",
            "status": "not_needed",
            "renderStatus": "not_needed",
            "videoUrl": None,
            "media_url": None,
            "usedFallback": False,
            "error": None,
            "timestamps": timestamps,
            "syncPlan": sync_plan,
        }
    code = (visual_payload.get("manimCode") or "").strip()
    code_source = (visual_payload.get("manimCodeSource") or "ai_generated" if code else "local_fallback")
    validation_error = direct_manim_validation_error(code) if code else "empty Manim code"
    visual_prompt = (visual_payload.get("visualPrompt") or visual_payload.get("manimPlan") or "").strip()
    logger.info(
        "[teaching-visual] visualNeeded=true visualType=manim code_chars=%s code_source=%s validation_error=%s visual_prompt_chars=%s session=%s title=%s",
        len(code),
        code_source,
        validation_error,
        len(visual_prompt),
        session_id,
        title,
    )
    speech_text = ((payload.get("speech") or {}).get("text") or payload.get("spoken_answer") or "").strip()
    visual_plan = payload.get("visualPlanWithTimestamps") or visual_payload.get("segments") or visual_payload.get("timestamps") or []
    if validation_error:
        logger.warning("[manim] generated code validation failed before render; attempting one repair session=%s title=%s error=%s", session_id, title, validation_error)
        repaired = await repair_manim_code_with_error(
            failed_code=code,
            error_log=validation_error,
            spoken_answer=speech_text,
            visual_plan=visual_plan if isinstance(visual_plan, list) else [],
            topic=(payload.get("teachingStateUpdate") or {}).get("current_topic") or title,
            title=title,
        )
        repaired_code = str(repaired.get("manim_code") or "").strip()
        repaired_error = direct_manim_validation_error(repaired_code) if repaired_code else repaired.get("error") or "empty repaired Manim code"
        if repaired_code and not repaired_error:
            code = repaired_code
            code_source = repaired.get("source") or "ai_repaired"
            validation_error = None
            logger.info("[manim] validation repair succeeded session=%s title=%s model=%s", session_id, title, repaired.get("model"))
        else:
            logger.warning("[manim] validation repair failed; using local fallback session=%s title=%s error=%s", session_id, title, repaired_error)
            code = build_fallback_manim_code(
                title=title,
                topic=(payload.get("teachingStateUpdate") or {}).get("current_topic") or title,
                spoken_answer=speech_text,
                visual_plan=visual_plan if isinstance(visual_plan, list) else [],
            )
            code_source = "local_fallback"
            validation_error = direct_manim_validation_error(code)

    sync_plan = payload.get("syncPlan") or {}
    renderer_payload = {
        "renderer_version": "openai_direct_manim_v1",
        "scene_type": "openai_direct",
        "scene_class_name": MANIM_SCENE_CLASS_NAME,
        "manim_code": code,
        "_disable_render_fallback": True,
        "title": title or "Visual explanation",
        "subtitle": subtitle or "Visual clarification",
        "duration_sec": 12,
        "segment_id": cache_segment_id,
        "student_query": student_query,
        "visual_prompt": visual_prompt,
        "manim_code_source": code_source,
    }
    try:
        try:
            rendered = await render_manim_payload_async(
                renderer_payload,
                segment_id=cache_segment_id,
                frame_number=1,
            )
        except Exception as first_exc:  # noqa: BLE001
            error_log = _extract_manim_error_log(first_exc)
            if code_source != "local_fallback":
                logger.warning("[manim] first render failed; attempting one AI repair session=%s title=%s error=%s", session_id, title, str(first_exc)[:300])
                repaired = await repair_manim_code_with_error(
                    failed_code=code,
                    error_log=error_log,
                    spoken_answer=speech_text,
                    visual_plan=visual_plan if isinstance(visual_plan, list) else [],
                    topic=(payload.get("teachingStateUpdate") or {}).get("current_topic") or title,
                    title=title,
                )
                repaired_code = str(repaired.get("manim_code") or "").strip()
                repaired_error = direct_manim_validation_error(repaired_code) if repaired_code else repaired.get("error") or "empty repaired Manim code"
                if repaired_code and not repaired_error:
                    repair_payload = {**renderer_payload, "manim_code": repaired_code, "manim_code_source": repaired.get("source") or "ai_repaired"}
                    try:
                        rendered = await render_manim_payload_async(
                            repair_payload,
                            segment_id=cache_segment_id,
                            frame_number=1,
                        )
                        renderer_payload = repair_payload
                        code_source = repair_payload["manim_code_source"]
                    except Exception as repair_exc:  # noqa: BLE001
                        logger.warning("[manim] repaired render failed; using local fallback session=%s title=%s error=%s", session_id, title, str(repair_exc)[:300])
                        raise repair_exc
                else:
                    logger.warning("[manim] repair response invalid; using local fallback session=%s title=%s error=%s", session_id, title, repaired_error)
                    raise first_exc
            else:
                raise first_exc
        if not rendered:
            raise RuntimeError("Manim render returned no result")
        media_url = rendered.get("video_url") or rendered.get("media_url") or rendered.get("public_url")
        validation = (rendered.get("payload") or {}).get("manim_code_validation") or rendered.get("validation")
        used_fallback = bool(rendered.get("used_fallback") or (validation or {}).get("fallback_used"))
        logger.info(
            "[manim] public_url=%s used_fallback=%s code_source=%s cache_hit=%s session=%s title=%s",
            media_url,
            used_fallback,
            code_source,
            bool(rendered.get("cache_hit")),
            session_id,
            title,
        )
        return {
            "type": "manim",
            "visualType": "manim",
            "status": "ready",
            "renderStatus": "ready",
            "videoUrl": media_url,
            "media_url": media_url,
            "usedFallback": used_fallback,
            "manimCodeSource": code_source,
            "error": None,
            "timestamps": timestamps,
            "syncPlan": sync_plan,
            "payload": {
                "media_url": media_url,
                "video_url": media_url,
                "duration_sec": renderer_payload["duration_sec"],
                "used_fallback": used_fallback,
                "manim_code_source": code_source,
                "validation": validation,
                "cache_hit": bool(rendered.get("cache_hit")),
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[manim] rendering generated/repaired code failed; rendering local fallback session=%s title=%s error=%s", session_id, title, exc)
        fallback_code = build_fallback_manim_code(
            title=title,
            topic=(payload.get("teachingStateUpdate") or {}).get("current_topic") or title,
            spoken_answer=speech_text,
            visual_plan=visual_plan if isinstance(visual_plan, list) else [],
        )
        fallback_payload = {
            **renderer_payload,
            "manim_code": fallback_code,
            "manim_code_source": "local_fallback",
            "_disable_render_fallback": False,
        }
        try:
            rendered = await render_manim_payload_async(
                fallback_payload,
                segment_id=cache_segment_id,
                frame_number=1,
            )
            media_url = rendered.get("video_url") or rendered.get("media_url") or rendered.get("public_url")
            logger.info("[manim] local fallback render succeeded session=%s title=%s url=%s", session_id, title, media_url)
            return {
                "type": "manim",
                "visualType": "manim",
                "status": "ready",
                "renderStatus": "ready",
                "videoUrl": media_url,
                "media_url": media_url,
                "usedFallback": True,
                "manimCodeSource": "local_fallback",
                "error": None,
                "timestamps": timestamps,
                "syncPlan": sync_plan,
                "payload": {
                    "media_url": media_url,
                    "video_url": media_url,
                    "duration_sec": fallback_payload["duration_sec"],
                    "used_fallback": True,
                    "manim_code_source": "local_fallback",
                    "cache_hit": bool(rendered.get("cache_hit")),
                },
            }
        except Exception as fallback_exc:  # noqa: BLE001
            logger.exception("[manim] local fallback render failed session=%s title=%s error=%s", session_id, title, fallback_exc)
        return {
            "type": "manim",
            "visualType": "manim",
            "status": "failed",
            "renderStatus": "failed",
            "videoUrl": None,
            "media_url": None,
            "usedFallback": False,
            "manimCodeSource": code_source,
            "error": "The Manim visual failed to render, but the spoken clarification is available.",
            "timestamps": timestamps,
            "syncPlan": sync_plan,
        }


async def _start_persona_only_teaching(session_id: str) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    sessions_repo.update(session_id, {"state": "persona_only_teaching", "mode": "persona_only"})
    return await _answer_persona_only(session_id, f"Please introduce {session.get('selected_topic') or 'this topic'} in your teaching style.")


async def _answer_persona_only(session_id: str, student_text: str) -> dict[str, Any]:
    session = sessions_repo.get(session_id)
    persona = _persona_for_session(session) or {}
    student = _student_for_session(session) or {}
    topic = session.get("selected_topic") or ""
    memory = _session_memory_for_prompt(session_id)
    previous_answer = str(memory.get("last_assistant_answer") or "")
    prompt_memory = dict(memory)
    if _intent_from_text(student_text) == "yes" and previous_answer:
        prompt_memory["student_intent"] = "affirmative_continue"
        logger.info(
            "persona-only affirmative follow-up mapped to continuation session=%s current_step=%s next_goal=%s",
            session_id,
            memory.get("current_step") or "",
            memory.get("next_teaching_goal") or "",
        )
    logger.info(
        "building persona-only prompt session=%s current_topic=%s current_step=%s recent_turns=%s previous_assistant_included=%s",
        session_id,
        memory.get("current_topic") or topic,
        memory.get("current_step") or "",
        len(memory.get("recent_turns") or []),
        bool(previous_answer),
    )
    payload = await generate_teaching_response_with_visuals(
        mode="persona_only_teaching",
        persona_prompt=persona.get("active_persona_prompt") or "",
        teacher_name=persona.get("teacher_name") or "",
        teacher_profession=persona.get("profession") or "",
        student_name=student.get("name") or "",
        topic=topic,
        student_query=student_text,
        current_roadmap_part=None,
        part_context=f"RECENT_CONVERSATION:\n{_history_excerpt(session_id)}",
        available_visual_mode="manim",
        session_memory=prompt_memory,
        previous_assistant_answer=previous_answer,
    )
    visual = await _render_teaching_visual(
        session_id,
        payload,
        student_query=student_text,
        title=topic or "Persona-only teaching",
        subtitle="Interactive Manim explanation",
        cache_segment_id=f"persona_only_{persona.get('id') or 'persona'}_{topic}_{student_text}"[:160],
    )
    speech_text = ((payload.get("speech") or {}).get("text") or "").strip()
    ask_followup = ((payload.get("teachingControl") or {}).get("askFollowUp") or payload.get("askFollowUp") or CLARIFICATION_FOLLOWUP)
    content = _append_followup(speech_text, ask_followup)
    msg = _record_message(
        session_id,
        "assistant",
        content,
        {
            "kind": "answer_persona_only",
            "source": "persona_only",
            "speech": payload.get("speech") or {},
            "syncPlan": payload.get("syncPlan") or {},
            "teachingStateUpdate": payload.get("teachingStateUpdate") or {},
            "visualPlanWithTimestamps": payload.get("visualPlanWithTimestamps") or [],
            "askFollowUp": ask_followup,
            "teachingControl": payload.get("teachingControl") or {},
            "debug": payload.get("debug") or {},
            "visual": visual,
        },
    )
    return _envelope(sessions_repo.get(session_id), msg, prompt_for="reply", visual=visual)


def _build_visual_hint(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not payload.get("visualNeeded"):
        return None
    return {
        "type": payload.get("visualType") or "none",
        "prompt": payload.get("visualPrompt") or "",
    }
