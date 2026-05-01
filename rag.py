from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from ai_prompts import (
    GREETING_RESPONSE_SCHEMA,
    GREETING_SYSTEM_PROMPT,
    LESSON_TUTOR_SYSTEM_PROMPT,
    NON_VIDEO_CONTEXT_MODE_PROMPT,
    PEDAGOGY_ADVANCE_PROMPT,
    PEDAGOGY_CLARIFY_PROMPT,
    PEDAGOGY_CONFIRM_ADVANCE_PROMPT,
    PEDAGOGY_DETAILED_PROMPT,
    PEDAGOGY_SIMPLE_PROMPT,
    TEACHER_RESPONSE_SCHEMA,
    VIDEO_CONTEXT_MODE_PROMPT,
    VIDEO_TEACHER_SYSTEM_PROMPT,
)
from blackboard_visuals import build_blackboard_visual_payload
from board_scene_library import is_valid_scene_object, is_valid_scene_slot
from config import DATA_DIR, GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY
from data_indexer import query_index
from backend.services.model_router import get_model_config, llm_json as routed_llm_json
from teaching_pipeline import (
    build_pipeline_board_actions,
    materialize_teaching_blueprint,
    prepare_teaching_blueprint,
    run_teaching_pipeline,
    stream_teaching_blueprint,
)

REMOTE_TEACHER_ENABLED = os.getenv("PARALLEA_ENABLE_REMOTE_TEACHER", "1" if (OPENAI_API_KEY or GEMINI_API_KEY or GROQ_API_KEY) else "0") == "1"
TEACHER_MODEL_CONFIG = get_model_config("answer")
TEACHER_PROVIDER = TEACHER_MODEL_CONFIG.provider
TEACHER_MODEL = TEACHER_MODEL_CONFIG.model
RELEVANCE_THRESHOLD = 0.65

logger = logging.getLogger("parallea.rag")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "briefly", "by", "can", "could", "did",
    "do", "does", "for", "from", "get", "give", "go", "help", "how", "i", "if", "in", "into", "is",
    "it", "its", "let", "like", "me", "more", "my", "of", "on", "or", "our", "please", "re", "show",
    "so", "step", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "through", "to", "up", "use", "used", "video", "walk", "what", "when", "where", "which",
    "why", "with", "would", "you", "your",
}

CLASSROOM_SITUATION = "Class_room"

VISUAL_SYSTEM = """You are a visual lesson planner for a synchronized classroom board.
Return valid JSON only. No markdown fences.
Create one coherent blackboard scene that looks like a strong teacher drew it by hand.
Use only the whiteboard renderer and the semantic scene payload contract.
The board must have exactly one segment.
Keep text compact and readable.
Visualize the meaning of the answer, not the wording.
Think like a teacher building a living blackboard scene with one main object and up to two support objects.
Put the primary meaning object in the center and use left or right for support views or real-world examples.
Use animated meaning-first objects when they fit, such as atom, blood_flow, lungs, neural_net, matrix, cartesian_plane, wave, vector_axes, beaker, walking_child, balloon_escape, boat_river, force_arrows, spring_mass, pendulum, planet_orbit, magnet_field, triangle, or process_chain.
When an abstract concept becomes clearer through a human-scale reference scene, prefer that reference scene over a generic note card.
Do not overlap major objects.
Use staged beats so the board reveals the explanation progressively as the audio moves.
Do not split the answer into multiple visual modes or multiple scenes.

Return exactly:
{
  "segments": [
    {
      "id": "seg_1",
      "title": "...",
      "start_pct": 0.0,
      "end_pct": 1.0,
      "kind": "whiteboard",
      "payload": {
        "style": "semantic_scene",
        "title": "...",
        "subtitle": "...",
        "objects": [
          {"id":"obj_1","kind":"matrix","slot":"center","label":"...","detail":"..."}
        ],
        "connectors": [
          {"from":"obj_1","to":"obj_2","label":"..."}
        ],
        "beats": [
          {"id":"beat_1","start_pct":0.0,"end_pct":0.33,"focus":["obj_1"],"caption":"..."}
        ]
      }
    }
  ]
}"""

GREETING_VISUAL = build_blackboard_visual_payload(
    title="Lesson board",
    question="How will this lesson work?",
    answer="Ask a question, hear the explanation, and follow one clear blackboard sketch.",
    focus_text="The board stays simple, readable, and synchronized with the spoken explanation.",
    supporting=["Ask anything in your own words.", "The explanation will stay conversational and visual."],
)


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2 and token not in STOPWORDS]


def trim_sentence(text: str, limit: int = 140) -> str:
    text = clean_spaces(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip()
    return (cut or text[:limit]).rstrip(".,;: ") + "..."


def sentence_case(text: str) -> str:
    text = clean_spaces(text)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def split_sentences(text: str, limit: int = 4) -> list[str]:
    parts = [clean_spaces(part) for part in re.split(r"(?<=[.!?])\s+", clean_spaces(text)) if clean_spaces(part)]
    if not parts and clean_spaces(text):
        parts = [clean_spaces(text)]
    return [sentence_case(trim_sentence(part, 140)) for part in parts[:limit]]


def creator_profession_text(value: str) -> str:
    return clean_spaces(value) or "educator"


def split_context_parts(context: str, limit: int = 5) -> list[str]:
    parts = [clean_spaces(part) for part in re.split(r"\n\s*\n", context or "") if clean_spaces(part)]
    if not parts and clean_spaces(context):
        parts = [clean_spaces(context)]
    return parts[:limit]


def format_context_parts(context: str, limit: int = 5) -> str:
    parts = split_context_parts(context, limit=limit)
    if not parts:
        return "1. Answer naturally from the creator's teaching knowledge and keep the explanation direct."
    return "\n".join(f"{idx}. {part}" for idx, part in enumerate(parts, start=1))


def format_lesson_context(lesson_context: str, limit: int = 2) -> str:
    lines = [sentence_case(trim_sentence(line, 120)) for line in lesson_context.splitlines() if clean_spaces(line)]
    return " ".join(lines[:limit]) or "the main ideas of this lesson together."


def trim_prompt_context(context: str, limit: int = 1600) -> str:
    return trim_sentence(clean_spaces(context), limit)


def compact_history(history: list[dict[str, str]], limit: int = 4) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for turn in history[-limit:]:
        role = clean_spaces(turn.get("role")).lower()
        if role not in {"user", "assistant"}:
            continue
        rows.append({"role": role, "content": trim_sentence(turn.get("content"), 220)})
    return rows


def clean_json(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    raw = str(raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return fallback


def build_viewer_profile(history: list[dict[str, str]]) -> dict[str, Any]:
    user_messages = [m["content"] for m in history if m.get("role") == "user"]
    profile = {"questions_asked": len(user_messages), "knowledge_level": "beginner", "topics_struggled": []}
    if len(user_messages) >= 6 or any(len(m.split()) > 25 for m in user_messages):
        profile["knowledge_level"] = "intermediate"
    return profile


def rerank_chunks(question: str, chunks: list[str], metadatas: list[dict], top_k: int = 3):
    if not chunks:
        return [], []
    pairs = []
    tokens = set(tokenize(question))
    for chunk, meta in zip(chunks, metadatas):
        chunk_tokens = set(tokenize(chunk))
        score = len(tokens & chunk_tokens)
        pairs.append((score, chunk, meta))
    ranked = sorted(pairs, key=lambda item: item[0], reverse=True)
    return [r[1] for r in ranked[:top_k]], [r[2] for r in ranked[:top_k]]


def load_local_chunks(chunks_path: str) -> list[dict[str, Any]]:
    path = Path(chunks_path)
    if not path.is_absolute() and not path.exists():
        path = DATA_DIR / path
    if path.is_dir():
        path = path / "chunks.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for key in sorted(data.keys(), key=lambda value: int(value)):
        item = data[key]
        rows.append(
            {
                "index": int(key),
                "start_sec": float(item.get("start_sec", 0.0)),
                "end_sec": float(item.get("end_sec", 0.0)),
                "text": clean_spaces(item.get("text", "")),
            }
        )
    return rows


def extract_video_id_from_chunks_path(chunks_path: str) -> str:
    text = clean_spaces(chunks_path)
    if not text:
        return ""
    match = re.search(r"video_([A-Za-z0-9_-]+)", text.replace("\\", "/"))
    return match.group(1) if match else ""


def _lexical_chunk_matches(question: str, local_chunks: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    q_tokens = set(tokenize(question))
    scored = []
    for item in local_chunks:
        score = len(q_tokens & set(tokenize(item["text"])))
        scored.append((score, item["text"], {"start_sec": item["start_sec"], "end_sec": item["end_sec"], "index": item["index"]}))
    ranked = sorted(scored, key=lambda row: row[0], reverse=True)
    matches = []
    for score, text, meta in ranked[: max(1, top_k)]:
        if score <= 0:
            continue
        matches.append(
            {
                "index": meta["index"],
                "start_sec": meta["start_sec"],
                "end_sec": meta["end_sec"],
                "text": text,
            }
        )
    return matches


def _top_persona_phrases(texts: list[str], limit: int = 3) -> list[str]:
    counts: dict[str, int] = {}
    for text in texts:
        tokens = tokenize(text)
        for size in (3, 2):
            for idx in range(0, max(0, len(tokens) - size + 1)):
                phrase = " ".join(tokens[idx : idx + size])
                if len(phrase) >= 8:
                    counts[phrase] = counts.get(phrase, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    phrases = [phrase for phrase, _ in ranked[:limit]]
    if phrases:
        return phrases
    singles = {}
    for text in texts:
        for token in tokenize(text):
            if len(token) < 5:
                continue
            singles[token] = singles.get(token, 0) + 1
    return [token for token, _ in sorted(singles.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def extract_persona_context(chunks_path: str) -> str:
    chunks = load_local_chunks(chunks_path)
    if not chunks:
        return ""
    selected = chunks[:3]
    for item in chunks[-3:]:
        if item not in selected:
            selected.append(item)
    texts = [clean_spaces(item.get("text")) for item in selected if clean_spaces(item.get("text"))]
    if not texts:
        return ""
    combined = " ".join(texts).lower()
    descriptors = []
    if any(term in combined for term in [" let's ", " you ", " your ", " we "]):
        descriptors.append("speaks in a conversational, learner-directed tone")
    else:
        descriptors.append("explains concepts in a direct classroom tone")
    if any(term in combined for term in [" like ", " as if ", " imagine "]):
        descriptors.append("uses analogies to ground abstract ideas")
    if any(term in combined for term in [" first ", " then ", " finally ", " step "]):
        descriptors.append("builds ideas step by step")
    phrases = _top_persona_phrases(texts, limit=3)
    phrase_text = ""
    if phrases:
        if len(phrases) == 1:
            phrase_text = f' and frequently uses phrases like "{phrases[0]}"'
        else:
            joined = ", ".join(f'"{item}"' for item in phrases[:-1]) + f', and "{phrases[-1]}"'
            phrase_text = f" and frequently uses phrases like {joined}"
    summary = f"This creator {'; '.join(descriptors)}{phrase_text}."
    return sentence_case(trim_sentence(summary, 220))


def build_context(question: str, chunks_path: str):
    timestamp = None
    timestamps: list[float] = []
    source = "video"
    context = ""

    local_chunks = load_local_chunks(chunks_path)
    matches: list[dict[str, Any]] = []
    video_id = extract_video_id_from_chunks_path(chunks_path)
    if video_id:
        try:
            matches = query_index(video_id, question, top_k=5)
        except Exception as exc:
            logger.warning("vector retrieval failed video_id=%s error=%s", video_id, exc)
            matches = []
    if not matches:
        matches = _lexical_chunk_matches(question, local_chunks, top_k=5)
    if matches:
        context = "\n\n".join(clean_spaces(item.get("text")) for item in matches if clean_spaces(item.get("text")))
        seen = set()
        for meta in matches:
            t = meta.get("start_sec")
            if t is not None and t not in seen:
                seen.add(t)
                timestamps.append(float(t))
        timestamp = timestamps[0] if timestamps else None
    else:
        source = "classroom"
        context = ""
    return context, source, timestamp, timestamps


def pack_teacher_response(
    answer: str,
    title: str = "Board sketch",
    follow_up: str = "Do you want me to slow that down, give an example, or compare it with something nearby?",
    suggestions: list[str] | None = None,
    confidence: str = "medium",
) -> dict[str, Any]:
    key_lines = split_sentences(answer, limit=3)
    board_actions = [
        {"type": "clear"},
        {"type": "title", "text": title},
        {"type": "bullet", "text": key_lines[0] if key_lines else "Start from the main idea."},
        {"type": "bullet", "text": key_lines[1] if len(key_lines) > 1 else "Track what changes from one step to the next."},
        {"type": "highlight", "text": key_lines[-1] if key_lines else "Keep the cause-and-effect chain in view."},
    ]
    return {
        "answer": answer,
        "follow_up": follow_up,
        "suggestions": suggestions or ["Explain more simply", "Give a concrete example", "Show the board version"],
        "confidence": confidence,
        "board_actions": board_actions,
    }


def local_topic_teacher_response(question: str) -> dict[str, Any] | None:
    tokens = set(tokenize(question))
    q = clean_spaces(question).lower()

    def has_any(*terms: str) -> bool:
        return any(term in tokens or term in q for term in terms)

    if has_any("lung", "lungs", "alveoli", "respiration", "breathing", "breathe"):
        answer = (
            "Your lungs work by exchanging gases between the air and your blood. "
            "When you breathe in, air travels down the windpipe into branching tubes until it reaches tiny air sacs called alveoli. "
            "Oxygen moves from those air sacs into the blood, while carbon dioxide moves from the blood into the air sacs. "
            "When you breathe out, that carbon-dioxide-rich air leaves the body. "
            "So the real job of the lungs is gas exchange, not just filling up like balloons."
        )
        return pack_teacher_response(
            answer,
            title="How Lungs Work",
            follow_up="Do you want me to connect that to blood flow or explain the alveoli more slowly?",
            suggestions=["Explain alveoli simply", "Connect lungs to blood flow", "Show gas exchange on the board"],
            confidence="high",
        )

    if has_any("heart", "circulation", "blood", "artery", "vein", "circulatory"):
        answer = (
            "Your heart works like a muscular pump that keeps blood moving in one direction through the body. "
            "The right side sends blood to the lungs to pick up oxygen, and the left side sends oxygen-rich blood out to the rest of the body. "
            "Arteries carry blood away from the heart, veins bring it back, and tiny capillaries are where the real exchange with tissues happens. "
            "That flow matters because cells need oxygen and nutrients constantly, and they also need waste removed. "
            "So circulation is really a delivery-and-cleanup system running all the time."
        )
        return pack_teacher_response(
            answer,
            title="Circulation",
            follow_up="Do you want the short version of the two sides of the heart, or the full circulation loop?",
            suggestions=["Left vs right side of heart", "Explain arteries and veins", "Show circulation visually"],
            confidence="high",
        )

    if has_any("force", "forces", "gravity", "friction", "motion", "newton", "push", "pull", "net force"):
        answer = (
            "When you analyze forces, do not ask only whether something is moving. "
            "Ask which pushes or pulls act on it and which one is stronger. "
            "If the forces balance, the motion stays steady or the object stays still, and if they do not balance, the motion changes. "
            "That is why arrows for gravity, friction, thrust, drag, or lift are so useful on the board. "
            "They let you see the net force instead of guessing from the motion alone."
        )
        return pack_teacher_response(
            answer,
            title="Forces",
            follow_up="Do you want me to use a walking child, a balloon, or a floating boat as the example?",
            suggestions=["Walking example", "Balloon example", "Boat example"],
            confidence="high",
        )

    if has_any("atom", "electron", "proton", "neutron", "nucleus"):
        answer = (
            "An atom has a tiny dense nucleus in the middle and electrons arranged around it. "
            "The nucleus contains protons and neutrons, so almost all of the atom's mass is packed into a very small space. "
            "Electrons are much lighter, and their arrangement controls how the atom behaves in chemical reactions. "
            "That is why two atoms can have different properties even though they are built from the same basic parts. "
            "So the key idea is structure: the center holds most of the mass, and the electron arrangement controls interaction."
        )
        return pack_teacher_response(
            answer,
            title="Atomic Structure",
            follow_up="Do you want me to connect that to bonding, charge, or energy levels next?",
            suggestions=["Explain the nucleus", "Explain electrons", "Show the atom visually"],
            confidence="high",
        )

    if has_any("neural network", "neural", "perceptron", "hidden layer", "model", "inference"):
        answer = (
            "A neural network works by turning the input into numbers and passing those numbers through layers of weighted connections. "
            "Each layer picks up patterns that are a little more meaningful than the last one. "
            "Early layers may detect simple features, while later layers combine them into more useful concepts for the task. "
            "During training, the network adjusts its weights so the output gets closer to the correct answer. "
            "So the real story is repeated transformation: numbers go in, patterns get refined, and a prediction comes out."
        )
        return pack_teacher_response(
            answer,
            title="Neural Network",
            follow_up="Do you want the version for images, text, or the training process itself?",
            suggestions=["Explain layers simply", "Relate it to images", "Show the signal flow"],
            confidence="high",
        )

    if has_any("matrix", "matrices", "tensor", "linear algebra", "vector", "vectors", "embedding"):
        answer = (
            "A matrix is just an organized grid of numbers, and that makes it useful because a computer can transform many values at once. "
            "In AI, images, word embeddings, and model weights are often stored in matrix or tensor form. "
            "Linear algebra then tells you how to rotate, scale, project, or combine those numbers in a clean mathematical way. "
            "That matters because the model is really learning relationships between numbers, not looking at pictures or words the way you do. "
            "So matrices are the structure that lets the machine represent and manipulate complex information efficiently."
        )
        return pack_teacher_response(
            answer,
            title="Matrix Meaning",
            follow_up="Do you want me to tie that to images, embeddings, or matrix multiplication?",
            suggestions=["Images as matrices", "Explain tensors simply", "Show matrix transformation"],
            confidence="high",
        )

    if has_any("photosynthesis", "chlorophyll", "plant", "plants", "sunlight"):
        answer = (
            "Photosynthesis is how plants turn light energy into stored chemical energy. "
            "They take in carbon dioxide from the air and water from the soil, then use sunlight captured by chlorophyll to drive the reaction. "
            "That process builds glucose, which stores energy, and it also releases oxygen as a by-product. "
            "So the plant is not just sitting in sunlight; it is using light to rearrange raw materials into food. "
            "That is why photosynthesis sits at the base of so many food chains."
        )
        return pack_teacher_response(
            answer,
            title="Photosynthesis",
            follow_up="Do you want the board version with the inputs and outputs, or the chloroplast version?",
            suggestions=["Inputs and outputs", "Explain chlorophyll", "Show photosynthesis visually"],
            confidence="high",
        )

    if has_any("electric", "electricity", "current", "voltage", "circuit", "resistor", "battery"):
        answer = (
            "Electric current is the flow of charge through a closed path. "
            "Voltage is what pushes that charge, and resistance is what makes the flow harder. "
            "A battery provides the push, the wires provide the path, and components like bulbs or resistors use the energy along the way. "
            "If the circuit is broken, the charges cannot keep moving around the loop. "
            "So the clean way to think about a circuit is push, path, and load."
        )
        return pack_teacher_response(
            answer,
            title="Electric Circuit",
            follow_up="Do you want me to compare voltage and current, or explain a simple bulb circuit?",
            suggestions=["Voltage vs current", "Simple circuit example", "Show current flow"],
            confidence="high",
        )

    if has_any("magnet", "magnetic", "field", "north pole", "south pole"):
        answer = (
            "A magnetic field is the region around a magnet where magnetic forces can act. "
            "Field lines are a visual tool that show the direction a north pole would move and where the pull is stronger or weaker. "
            "The field is strongest near the poles, and opposite poles attract while like poles repel. "
            "So the field is not a visible object by itself; it is a way to map the influence of the magnet through space. "
            "That is why drawing the field lines often makes the idea click much faster."
        )
        return pack_teacher_response(
            answer,
            title="Magnetic Field",
            follow_up="Do you want me to connect that to poles, field lines, or electromagnets?",
            suggestions=["Explain field lines", "North and south poles", "Show a magnet on the board"],
            confidence="high",
        )

    if has_any("boat", "buoyancy", "float", "floating", "river"):
        answer = (
            "A boat floats because the water pushes upward on it with a buoyant force. "
            "As the boat settles into the water, it displaces some of that water, and the upward push grows until it balances the boat's weight. "
            "If the upward buoyant force matches the downward weight, the boat floats instead of sinking. "
            "The river current can still carry the boat sideways or downstream, but that is separate from why it stays up. "
            "So one idea explains floating, and another explains drifting."
        )
        return pack_teacher_response(
            answer,
            title="Why Boats Float",
            follow_up="Do you want me to separate buoyancy from current with a simple board sketch?",
            suggestions=["Explain buoyancy simply", "Weight vs buoyancy", "Show boat and river"],
            confidence="high",
        )

    if has_any("pendulum", "oscillation", "swing", "period"):
        answer = (
            "A pendulum swings because gravity keeps pulling the bob back toward the lowest point. "
            "When you release it from one side, gravitational potential energy changes into kinetic energy as it speeds up. "
            "At the bottom it is moving fastest, and on the way up the other side that kinetic energy turns back into potential energy. "
            "So the motion keeps trading energy between position and speed. "
            "That is why a pendulum is such a clean example of periodic motion."
        )
        return pack_teacher_response(
            answer,
            title="Pendulum Motion",
            follow_up="Do you want me to focus on energy exchange or the timing of the swing?",
            suggestions=["Energy in a pendulum", "Explain periodic motion", "Show the swing visually"],
            confidence="high",
        )

    if has_any("cell", "cells", "membrane", "cytoplasm", "organelle", "nucleus"):
        answer = (
            "A cell is a tiny living system that takes in materials, uses energy, and keeps its inside conditions under control. "
            "The membrane acts like a selective boundary, the cytoplasm is where many reactions happen, and specialized organelles handle different jobs. "
            "In many cells, the nucleus stores the DNA instructions that help control what the cell builds and does. "
            "So a cell is not just a bag of fluid; it is an organized working unit. "
            "That organization is what makes growth, repair, and reproduction possible."
        )
        return pack_teacher_response(
            answer,
            title="Cell Basics",
            follow_up="Do you want me to compare the cell to a factory, or go organelle by organelle?",
            suggestions=["Cell as a factory", "Explain the membrane", "Show the cell structure"],
            confidence="high",
        )

    if has_any("dna", "gene", "genes", "genetic", "chromosome"):
        answer = (
            "DNA stores biological instructions in a coded molecular form. "
            "A gene is a section of that DNA that carries instructions for building a protein or controlling a process. "
            "Cells read those instructions and use them to build structures, run reactions, and regulate growth. "
            "So DNA is not a trait by itself; it is the information blueprint behind the trait. "
            "That is why changes in DNA can change how the body develops or functions."
        )
        return pack_teacher_response(
            answer,
            title="DNA and Genes",
            follow_up="Do you want me to connect genes to proteins, or genes to inherited traits?",
            suggestions=["Genes vs DNA", "DNA to protein", "Show the information flow"],
            confidence="high",
        )

    return None


def heuristic_teacher_response(question: str, context: str) -> dict[str, Any]:
    topic_response = local_topic_teacher_response(question)
    if topic_response:
        return topic_response
    parts = split_context_parts(context, limit=3)
    anchor = sentence_case(trim_sentence(parts[0], 180)) if parts else ""
    support = sentence_case(trim_sentence(parts[1], 150)) if len(parts) > 1 else ""
    q = question.lower()
    if anchor:
        if "why" in q:
            answer = (
                f"Here is the idea. {anchor} "
                f"{support or 'Follow the cause first, then the effect it produces.'} "
                "That cause-and-effect chain is the part you want to hold onto."
            )
        elif any(term in q for term in ["compare", "difference", "vs", "versus"]):
            answer = (
                f"Here is the clean comparison. {anchor} "
                f"{support or 'Now compare what stays fixed with what changes across the two cases.'} "
                "That contrast usually tells you more than the names alone."
            )
        else:
            answer = (
                f"It works like this. {anchor} "
                f"{support or 'Then watch what changes from one step to the next until the outcome makes sense.'} "
                "Once you keep that sequence in mind, the idea becomes much easier to explain."
            )
        return pack_teacher_response(answer)
    if "why" in q:
        answer = (
            f"Here is the real reason. {anchor or 'The key is the structure underneath the idea, not just the label.'} "
            "Once that structure clicks for you, the rest stops feeling random and starts feeling predictable. "
            f"{support or 'That is why teachers keep returning to the same core pattern from different angles.'}"
        )
    elif "example" in q:
        answer = (
            f"A simple way to see it is this. {anchor or 'Start with one concrete case and watch what changes from input to outcome.'} "
            f"{support or 'Then reuse the same pattern on a second example so the idea stops feeling abstract to you.'} "
            "That is usually the point where the concept becomes usable instead of just familiar."
        )
    elif any(term in q for term in ["compare", "difference", "vs", "versus"]):
        answer = (
            f"The clean comparison is this. {anchor or 'One side tells you what stays fixed, and the other shows what actually changes.'} "
            f"{support or 'So do not compare the names first; compare the role each piece plays in the whole explanation.'} "
            "That gives you a much more reliable mental model."
        )
    else:
        answer = (
            "Think of it in three parts. First ask what goes in, then ask what changes in the middle, and finally ask what comes out at the end. "
            "That sequence gives you a real mechanism instead of a definition to memorize. "
            "Once you can tell the story in that order, the concept usually stops feeling abstract."
        )
    return pack_teacher_response(answer)


async def call_teacher(
    system: str,
    history: list[dict[str, str]],
    question: str,
    context: str,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or heuristic_teacher_response(question, context)
    if not REMOTE_TEACHER_ENABLED:
        return fallback
    try:
        messages = compact_history(history, limit=4)
        context_hint = trim_prompt_context(context, limit=1500)
        user_prompt = "\n\n".join(
            [
                TEACHER_RESPONSE_SCHEMA,
                "Conversation history:",
                json.dumps(messages, ensure_ascii=False),
                "Relevant classroom context:",
                context_hint or "No extra context.",
                "Student question:",
                trim_sentence(question, 260),
            ]
        )
        raw = await routed_llm_json("answer", system, user_prompt, temperature=0.5, max_tokens=900)
        parsed = clean_json(raw, fallback)
        if not parsed.get("answer"):
            return fallback
        return parsed
    except Exception as exc:
        logger.exception(
            "Teacher call failed provider=%s model=%s question=%s error=%s",
            TEACHER_PROVIDER,
            TEACHER_MODEL,
            trim_sentence(question, 160),
            exc,
        )
        return fallback


def pedagogy_prompt_text(mode: str) -> str:
    mode_name = clean_spaces(mode).lower()
    if mode_name == "detailed":
        return PEDAGOGY_DETAILED_PROMPT
    if mode_name == "clarify":
        return PEDAGOGY_CLARIFY_PROMPT
    if mode_name == "confirm_advance":
        return PEDAGOGY_CONFIRM_ADVANCE_PROMPT
    if mode_name == "advance":
        return PEDAGOGY_ADVANCE_PROMPT
    return PEDAGOGY_SIMPLE_PROMPT


def mode_prompt_text(use_video_context: bool) -> str:
    return VIDEO_CONTEXT_MODE_PROMPT if use_video_context else NON_VIDEO_CONTEXT_MODE_PROMPT


def pedagogy_follow_up(mode: str, default: str = "What should I expand next?") -> str:
    mode_name = clean_spaces(mode).lower()
    if mode_name == "detailed":
        return "Does that make sense now, or should I explain it a different way?"
    if mode_name == "clarify":
        return "Does this version make more sense now, or should I try one more angle?"
    if mode_name == "confirm_advance":
        return "Do you want to move to the next part, or stay here for one more example?"
    if mode_name == "advance":
        return "Does this next part make sense, or should I slow it down?"
    return "Would you like a deeper explanation, or does that already make sense?"


def pedagogy_suggestions(mode: str, *, use_video_context: bool = True) -> list[str]:
    mode_name = clean_spaces(mode).lower()
    if mode_name == "detailed":
        return ["That makes sense", "Explain it more simply", "Give another example"]
    if mode_name == "clarify":
        return ["I get it now", "Try one more example", "Explain even more simply"]
    if mode_name == "confirm_advance":
        return ["Move to the next part", "Give one more example", "Stay on this part"]
    if mode_name == "advance":
        return ["I understand", "Go deeper here", "Relate it to the source" if use_video_context else "Give an example"]
    return ["Explain in more detail", "I understand", "Give me an example"]


def heuristic_visual_payload(question: str, answer: str, context: str = "", title: str = "Blackboard plan") -> dict[str, Any]:
    return build_blackboard_visual_payload(
        title=title,
        question=question,
        answer=answer,
        focus_text=split_context_parts(context, limit=1)[0] if split_context_parts(context, limit=1) else answer,
        supporting=split_context_parts(context, limit=2)[1:],
    )


def sanitize_scene_object(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    kind = clean_spaces(obj.get("kind"))
    slot = clean_spaces(obj.get("slot"))
    if not is_valid_scene_object(kind) or not is_valid_scene_slot(slot):
        return None
    cleaned = {
        "id": clean_spaces(obj.get("id")) or f"{kind}_{slot}",
        "kind": kind,
        "slot": slot,
        "label": trim_sentence(clean_spaces(obj.get("label")) or kind.replace("_", " ").title(), 42),
        "detail": trim_sentence(clean_spaces(obj.get("detail")), 80),
    }
    return cleaned


def sanitize_connector(connector: Any, valid_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(connector, dict):
        return None
    from_id = clean_spaces(connector.get("from"))
    to_id = clean_spaces(connector.get("to"))
    if from_id not in valid_ids or to_id not in valid_ids or from_id == to_id:
        return None
    return {
        "from": from_id,
        "to": to_id,
        "label": trim_sentence(clean_spaces(connector.get("label")) or "connects to", 28),
    }


def sanitize_beat(beat: Any, valid_ids: set[str], idx: int) -> dict[str, Any] | None:
    if not isinstance(beat, dict):
        return None
    focus = [clean_spaces(item) for item in (beat.get("focus") or []) if clean_spaces(item) in valid_ids]
    if not focus:
        return None
    start_pct = max(0.0, min(1.0, float(beat.get("start_pct", 0.0))))
    end_pct = max(start_pct, min(1.0, float(beat.get("end_pct", 1.0))))
    return {
        "id": clean_spaces(beat.get("id")) or f"beat_{idx}",
        "start_pct": start_pct,
        "end_pct": end_pct,
        "focus": focus[:3],
        "caption": trim_sentence(clean_spaces(beat.get("caption")) or "Follow the main idea.", 96),
    }


def normalize_visual_payload(
    payload: dict[str, Any],
    question: str,
    answer: str,
    context: str,
    title: str,
) -> dict[str, Any]:
    fallback = heuristic_visual_payload(question, answer, context=context, title=title)
    segments = payload.get("segments") or []
    whiteboard = next((segment for segment in segments if isinstance(segment, dict) and segment.get("kind") == "whiteboard"), None)
    if not whiteboard:
        return fallback
    raw_payload = whiteboard.get("payload") or {}
    if clean_spaces(raw_payload.get("style")) != "semantic_scene":
        return fallback
    objects = [cleaned for cleaned in (sanitize_scene_object(item) for item in (raw_payload.get("objects") or [])) if cleaned]
    if not objects:
        return fallback
    unique_objects = []
    used_slots = set()
    used_ids = set()
    for obj in objects:
        if obj["slot"] in used_slots or obj["id"] in used_ids:
            continue
        used_slots.add(obj["slot"])
        used_ids.add(obj["id"])
        unique_objects.append(obj)
    if not unique_objects:
        return fallback
    valid_ids = {obj["id"] for obj in unique_objects}
    connectors = [cleaned for cleaned in (sanitize_connector(item, valid_ids) for item in (raw_payload.get("connectors") or [])) if cleaned][:2]
    beats = [cleaned for cleaned in (sanitize_beat(item, valid_ids, idx + 1) for idx, item in enumerate(raw_payload.get("beats") or [])) if cleaned]
    if not beats:
        return fallback
    beats.sort(key=lambda item: item["start_pct"])
    last_end = 0.0
    for beat in beats:
        beat["start_pct"] = max(last_end, beat["start_pct"])
        beat["end_pct"] = max(beat["start_pct"], beat["end_pct"])
        last_end = beat["end_pct"]
    beats[-1]["end_pct"] = 1.0
    return {
        "segments": [
            {
                "id": whiteboard.get("id") or "seg_1",
                "title": trim_sentence(whiteboard.get("title") or title or "Blackboard", 48),
                "start_pct": 0.0,
                "end_pct": 1.0,
                "kind": "whiteboard",
                "payload": {
                    "style": "semantic_scene",
                    "title": trim_sentence(raw_payload.get("title") or title or "Lesson board", 56),
                    "subtitle": trim_sentence(raw_payload.get("subtitle") or answer, 96),
                    "objects": unique_objects[:3],
                    "connectors": connectors,
                    "beats": beats[:4],
                },
            }
        ]
    }


async def call_visual_planner(
    question: str,
    answer: str,
    context: str,
    level: str,
    creator_name: str,
    creator_profession: str,
    video_title: str,
) -> dict[str, Any]:
    del level, creator_name, creator_profession
    return heuristic_visual_payload(question, answer, context=context, title=video_title)


def build_board_actions(question: str, answer: str, context: str) -> list[dict[str, str]]:
    answer_lines = split_sentences(answer, limit=3)
    focus = trim_sentence((split_context_parts(context, limit=1) or [answer])[0], 110)
    actions = [
        {"type": "clear"},
        {"type": "title", "text": "Classroom sketch"},
        {"type": "bullet", "text": answer_lines[0] if answer_lines else sentence_case(focus or "Start from the main idea.")},
    ]
    if any(term in question.lower() for term in ["compare", "difference", "vs", "versus"]):
        actions.append({"type": "bullet", "text": "Compare what stays fixed, what changes, and why that difference matters."})
    elif "why" in question.lower():
        actions.append({"type": "bullet", "text": "Follow the cause first, then the consequence, then the takeaway."})
    else:
        actions.append({"type": "bullet", "text": answer_lines[1] if len(answer_lines) > 1 else sentence_case(focus)})
    actions.append({"type": "highlight", "text": answer_lines[-1] if answer_lines else "The board should make the idea feel easy to track."})
    return actions


def heuristic_greeting_response(
    creator_name: str,
    creator_profession: str,
    video_title: str,
    lesson_context: str,
) -> dict[str, Any]:
    profession = creator_profession_text(creator_profession)
    lesson_summary = format_lesson_context(lesson_context, limit=2)
    greeting = (
        f'I am the AI clone of "{creator_name}" for the video "{video_title}". '
        f'Today we are going to learn {lesson_summary} '
        f'Ask me anything the way you would ask a real {profession} in class, and I will walk through it on the board with you.'
    )
    return {
        "greeting": greeting,
        "suggestions": ["Give me the big picture", "Start with the first important idea", "What should I notice first?"],
        "board_actions": [
            {"type": "clear"},
            {"type": "title", "text": video_title or "Lesson"},
            {"type": "bullet", "text": sentence_case(trim_sentence(lesson_summary, 110))},
            {"type": "bullet", "text": "Ask naturally, and I will explain it like a real classroom teacher."},
            {"type": "highlight", "text": "One clear board sketch at a time."},
        ],
    }


async def call_greeting(
    creator_name: str,
    creator_profession: str,
    video_title: str,
    lesson_context: str,
) -> dict[str, Any]:
    fallback = heuristic_greeting_response(creator_name, creator_profession, video_title, lesson_context)
    if not REMOTE_TEACHER_ENABLED:
        logger.info(
            "Greeting using local fallback provider=local model=%s reason=remote-disabled video_title=%s",
            TEACHER_MODEL,
            trim_sentence(video_title, 120),
        )
        return fallback
    profession = creator_profession_text(creator_profession)
    prompt = f"""{GREETING_RESPONSE_SCHEMA}

You are "{creator_name}" twin who is a Professional "{profession}" teaching "{video_title}".
Situation "{CLASSROOM_SITUATION}".
Lesson context in two lines:
{lesson_context or "Keep the opening focused on the main lesson idea and invite questions naturally."}

Write the short spoken greeting the teacher would actually say when class begins.
Include that you are the AI clone of the creator for this video.
Invite questions naturally.
"""
    try:
        raw = await routed_llm_json("answer", GREETING_SYSTEM_PROMPT, trim_sentence(prompt, 1800), temperature=0.55, max_tokens=350)
        parsed = clean_json(raw, fallback)
        if not parsed.get("greeting"):
            logger.warning(
                "Greeting remote response missing greeting field; using local fallback provider=%s model=%s video_title=%s",
                TEACHER_PROVIDER,
                TEACHER_MODEL,
                trim_sentence(video_title, 120),
            )
            return fallback
        return parsed
    except Exception as exc:
        logger.exception(
            "Greeting remote generation failed provider=%s model=%s video_title=%s; using local fallback error=%s",
            TEACHER_PROVIDER,
            TEACHER_MODEL,
            trim_sentence(video_title, 120),
            exc,
        )
        return fallback


async def get_teaching_blueprint_async(
    question: str,
    creator_name: str = "the creator",
    creator_profession: str = "educator",
    video_title: str = "this video",
    chunks_path: str = "data/chunks.json",
    conversation_history: list[dict[str, str]] | None = None,
    trigger: str | None = None,
    use_video_context: bool = True,
    pedagogy_mode: str = "simple",
    learner_request: str | None = None,
    topic_question: str | None = None,
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del creator_name, creator_profession, trigger
    conversation_history = conversation_history or []
    active_question = clean_spaces(topic_question or question) or question
    learner_turn = clean_spaces(learner_request or question) or question
    persona_context = extract_persona_context(chunks_path)
    if use_video_context:
        context, source, timestamp, timestamps = build_context(active_question, chunks_path)
    else:
        context, source, timestamp, timestamps = "", "classroom", None, []
    fallback_teacher = heuristic_teacher_response(active_question, context)
    try:
        blueprint = await prepare_teaching_blueprint(
            question=active_question,
            context=context,
            title=video_title,
            conversation_history=conversation_history,
            fallback_answer=fallback_teacher.get("answer", ""),
            fallback_follow_up=pedagogy_follow_up(pedagogy_mode, fallback_teacher.get("follow_up", "What should I expand next?")),
            fallback_suggestions=pedagogy_suggestions(pedagogy_mode, use_video_context=use_video_context),
            learner_request=learner_turn,
            topic_question=active_question,
            context_mode="video_context" if use_video_context else "non_video_context",
            pedagogy_mode=pedagogy_mode,
            persona_context=persona_context,
            preferred_visualization=preferred_visualization,
            session_state=session_state,
        )
        return {
            **blueprint,
            "timestamp": timestamp,
            "timestamps": timestamps,
            "source": "video" if source == "video" else "classroom",
            "context": context,
            "context_label": f"{video_title} | {'video context' if use_video_context else 'direct teaching'}",
            "persona_context": persona_context,
        }
    except Exception as exc:
        logger.exception("Teaching pipeline failed question=%s error=%s", trim_sentence(question, 160), exc)

    return {
        "answer": fallback_teacher.get("answer", ""),
        "follow_up": pedagogy_follow_up(pedagogy_mode, fallback_teacher.get("follow_up", "What should I expand next?")),
        "suggestions": pedagogy_suggestions(pedagogy_mode, use_video_context=use_video_context),
        "lesson_plan": None,
        "segment_plan": {"segments": []},
        "teaching_segments": [],
        "frame_sequence": [],
        "visual_payload": {"segments": []},
        "timestamp": timestamp,
        "timestamps": timestamps,
        "source": "video" if source == "video" else "classroom",
        "context": context,
        "context_label": f"{video_title} | {'video context' if use_video_context else 'direct teaching'}",
        "persona_context": persona_context,
    }


async def stream_teaching_blueprint_async(
    question: str,
    creator_name: str = "the creator",
    creator_profession: str = "educator",
    video_title: str = "this video",
    chunks_path: str = "data/chunks.json",
    conversation_history: list[dict[str, str]] | None = None,
    trigger: str | None = None,
    use_video_context: bool = True,
    pedagogy_mode: str = "simple",
    learner_request: str | None = None,
    topic_question: str | None = None,
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
):
    del creator_name, creator_profession, trigger
    conversation_history = conversation_history or []
    active_question = clean_spaces(topic_question or question) or question
    learner_turn = clean_spaces(learner_request or question) or question
    persona_context = extract_persona_context(chunks_path)
    if use_video_context:
        context, source, timestamp, timestamps = build_context(active_question, chunks_path)
    else:
        context, source, timestamp, timestamps = "", "classroom", None, []
    fallback_teacher = heuristic_teacher_response(active_question, context)
    async for event in stream_teaching_blueprint(
        question=active_question,
        context=context,
        title=video_title,
        conversation_history=conversation_history,
        fallback_answer=fallback_teacher.get("answer", ""),
        fallback_follow_up=pedagogy_follow_up(pedagogy_mode, fallback_teacher.get("follow_up", "What should I expand next?")),
        fallback_suggestions=pedagogy_suggestions(pedagogy_mode, use_video_context=use_video_context),
        learner_request=learner_turn,
        topic_question=active_question,
        context_mode="video_context" if use_video_context else "non_video_context",
        pedagogy_mode=pedagogy_mode,
        persona_context=persona_context,
        preferred_visualization=preferred_visualization,
        session_state=session_state,
    ):
        if event.get("event") == "blueprint" and isinstance(event.get("data"), dict):
            yield {
                "event": "blueprint",
                "data": {
                    **event["data"],
                    "timestamp": timestamp,
                    "timestamps": timestamps,
                    "source": "video" if source == "video" else "classroom",
                    "context": context,
                    "context_label": f"{video_title} | {'video context' if use_video_context else 'direct teaching'}",
                    "persona_context": persona_context,
                },
            }
            continue
        yield event


async def get_teaching_response_async(
    question: str,
    creator_name: str = "the creator",
    creator_profession: str = "educator",
    video_title: str = "this video",
    chunks_path: str = "data/chunks.json",
    conversation_history: list[dict[str, str]] | None = None,
    trigger: str | None = None,
    use_video_context: bool = True,
    pedagogy_mode: str = "simple",
    learner_request: str | None = None,
    topic_question: str | None = None,
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversation_history = conversation_history or []
    blueprint = await get_teaching_blueprint_async(
        question=question,
        creator_name=creator_name,
        creator_profession=creator_profession,
        video_title=video_title,
        chunks_path=chunks_path,
        conversation_history=conversation_history,
        trigger=trigger,
        use_video_context=use_video_context,
        pedagogy_mode=pedagogy_mode,
        learner_request=learner_request,
        topic_question=topic_question,
        preferred_visualization=preferred_visualization,
        session_state=session_state,
    )
    active_question = clean_spaces(topic_question or question) or question
    context = clean_spaces(blueprint.get("context"))
    persona_context = clean_spaces(blueprint.get("persona_context"))
    fallback_teacher = heuristic_teacher_response(active_question, context)
    try:
        pipeline = await materialize_teaching_blueprint(active_question, blueprint)
        answer = clean_spaces(pipeline.get("answer")) or fallback_teacher["answer"]
        visual_payload = pipeline.get("visual_payload") or heuristic_visual_payload(active_question, answer, context=context, title=video_title)
        return {
            "answer": answer,
            "follow_up": clean_spaces(pipeline.get("follow_up")) or pedagogy_follow_up(pedagogy_mode),
            "suggestions": pipeline.get("suggestions") or pedagogy_suggestions(pedagogy_mode, use_video_context=use_video_context),
            "timestamp": blueprint.get("timestamp"),
            "timestamps": blueprint.get("timestamps", []),
            "source": blueprint.get("source", "classroom"),
            "board_actions": build_pipeline_board_actions(pipeline) or build_board_actions(active_question, answer, context),
            "visual_payload": visual_payload,
            "lesson_plan": pipeline.get("lesson_plan"),
            "teaching_segments": pipeline.get("teaching_segments", []),
            "frame_sequence": pipeline.get("frame_sequence", []),
            "context_label": blueprint.get("context_label"),
            "teaching_session_state": pipeline.get("teaching_session_state"),
            "pipeline_debug": pipeline.get("pipeline_debug"),
        }
    except Exception as exc:
        logger.exception("Teaching materialization failed question=%s error=%s", trim_sentence(question, 160), exc)

    return {
        "answer": fallback_teacher["answer"],
        "follow_up": pedagogy_follow_up(pedagogy_mode),
        "suggestions": pedagogy_suggestions(pedagogy_mode, use_video_context=use_video_context),
        "timestamp": blueprint.get("timestamp"),
        "timestamps": blueprint.get("timestamps", []),
        "source": blueprint.get("source", "classroom"),
        "board_actions": build_board_actions(active_question, fallback_teacher["answer"], context),
        "visual_payload": heuristic_visual_payload(active_question, fallback_teacher["answer"], context=context, title=video_title),
        "context_label": blueprint.get("context_label"),
        "pipeline_debug": {"provider": "local_fallback_after_openai_pipeline_failure"},
    }


def lesson_focus_line(current_section_content: str, visible_metadata: list[str]) -> str:
    for item in visible_metadata:
        lower = item.lower()
        if any(term in lower for term in ["practice", "focus", "finger", "strum", "tune", "listen", "peg", "note", "chord"]):
            return sentence_case(trim_sentence(item, 120))
    return sentence_case(trim_sentence(current_section_content, 150))


def lesson_fallback_teacher_response(
    question: str,
    lesson_title: str,
    current_section_title: str,
    current_section_content: str,
    visible_metadata: list[str],
    trigger: str | None,
) -> dict[str, Any]:
    q = clean_spaces(question).lower()
    trigger_name = clean_spaces(trigger).lower()
    focus_line = lesson_focus_line(current_section_content, visible_metadata)
    section = current_section_title or "this part"
    lesson = lesson_title or "this lesson"

    if trigger_name == "lesson_open":
        answer = (
            f"Welcome to {lesson}. We are starting with {section}. "
            f"{sentence_case(trim_sentence(current_section_content, 150))} "
            f"{focus_line} Keep it simple and stay with this first part."
        )
        follow_up = "When you are ready, ask me a question or let me guide the first move."
    elif trigger_name == "section_change":
        answer = (
            f"We just moved to {section}. "
            f"{sentence_case(trim_sentence(current_section_content, 150))} "
            f"{focus_line} Start there before you worry about speed."
        )
        follow_up = "Do you want a quick explanation, a practice task, or a short check on this part?"
    elif trigger_name == "practice" or any(term in q for term in ["practice", "exercise", "drill"]):
        answer = (
            f"Try this for {section}. {focus_line} "
            "Do five slow repetitions, reset your hands, then do five more with cleaner timing."
        )
        follow_up = "Want me to make that easier, harder, or more rhythmic?"
    elif trigger_name == "test" or any(term in q for term in ["test", "quiz", "check"]):
        answer = (
            f"Quick check on {section}. Tell me the main idea in your own words, then show me the hand move or sound this part is asking for. "
            "A good answer names the idea and the action together."
        )
        follow_up = "Answer out loud and I will check it like a teacher."
    elif trigger_name == "summarize" or any(term in q for term in ["summary", "summarize", "recap"]):
        answer = (
            f"Short version. In {section}, the key idea is this. "
            f"{sentence_case(trim_sentence(current_section_content, 150))} "
            f"{focus_line}"
        )
        follow_up = "Do you want the same part explained more slowly or turned into a practice cue?"
    elif trigger_name == "repeat":
        answer = (
            f"Let me say {section} more slowly. "
            f"{sentence_case(trim_sentence(current_section_content, 150))} "
            f"{focus_line} One clean move at a time is enough here."
        )
        follow_up = "If you want, I can say it again with more hand-position detail."
    else:
        answer = (
            f"Stay with {section}. "
            f"{sentence_case(trim_sentence(current_section_content, 150))} "
            f"{focus_line} Keep your attention on this part of the lesson first."
        )
        follow_up = "Do you want a clearer explanation, a practice step, or a quick check?"

    return {
        "answer": answer,
        "follow_up": follow_up,
        "suggestions": [
            "Explain this part",
            "Give me a practice exercise",
            "Test my understanding",
        ],
    }


def looks_like_generic_lesson_fallback(answer: str) -> bool:
    text = clean_spaces(answer).lower()
    return (
        text.startswith("it works like this.")
        or "lesson title:" in text
        or "current section:" in text
        or "section position:" in text
        or "then watch what changes from one step to the next" in text
    )


async def get_lesson_teacher_response_async(
    question: str,
    lesson_title: str,
    lesson_description: str,
    current_section_title: str,
    current_section_content: str,
    section_index: int,
    total_sections: int,
    section_order: list[str] | None = None,
    visible_metadata: list[str] | None = None,
    timestamps: list[float] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    trigger: str | None = None,
    pedagogy_mode: str = "simple",
    learner_request: str | None = None,
    topic_question: str | None = None,
    preferred_visualization: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversation_history = conversation_history or []
    section_order = [clean_spaces(item) for item in (section_order or []) if clean_spaces(item)]
    visible_metadata = [clean_spaces(item) for item in (visible_metadata or []) if clean_spaces(item)]
    timestamps = [float(item) for item in (timestamps or [])]
    active_question = clean_spaces(topic_question or question) or question
    learner_turn = clean_spaces(learner_request or question) or question
    fallback = lesson_fallback_teacher_response(
        question=active_question,
        lesson_title=lesson_title,
        current_section_title=current_section_title,
        current_section_content=current_section_content,
        visible_metadata=visible_metadata,
        trigger=trigger,
    )
    context = "\n".join(
        [
            f"Lesson title: {lesson_title}",
            f"Lesson description: {lesson_description}",
            f"Current section: {current_section_title}",
            f"Section position: {section_index + 1} of {max(total_sections, 1)}",
            f"Section order: {' | '.join(section_order) if section_order else current_section_title}",
            f"Current section content: {current_section_content}",
            "Visible lesson details:",
            *[f"- {item}" for item in visible_metadata[:8]],
        ]
    )
    try:
        pipeline = await run_teaching_pipeline(
            question=active_question,
            context=context,
            title=current_section_title or lesson_title,
            conversation_history=conversation_history,
            fallback_answer=fallback["answer"],
            fallback_follow_up=pedagogy_follow_up(pedagogy_mode, fallback["follow_up"]),
            fallback_suggestions=pedagogy_suggestions(pedagogy_mode, use_video_context=False),
            learner_request=learner_turn,
            topic_question=active_question,
            context_mode="non_video_context",
            pedagogy_mode=pedagogy_mode,
            preferred_visualization=preferred_visualization,
            session_state=session_state,
        )
        answer = clean_spaces(pipeline.get("answer")) or fallback["answer"]
        if looks_like_generic_lesson_fallback(answer):
            answer = fallback["answer"]
        follow_up = clean_spaces(pipeline.get("follow_up")) or pedagogy_follow_up(pedagogy_mode, fallback["follow_up"])
        suggestions = [clean_spaces(item) for item in (pipeline.get("suggestions") or []) if clean_spaces(item)] or pedagogy_suggestions(pedagogy_mode, use_video_context=False)
        return {
            "answer": answer,
            "follow_up": follow_up,
            "suggestions": suggestions[:4],
            "timestamp": timestamps[0] if timestamps else None,
            "timestamps": timestamps[:4],
            "source": "lesson",
            "board_actions": build_pipeline_board_actions(pipeline),
            "visual_payload": pipeline.get("visual_payload") or {"segments": []},
            "lesson_plan": pipeline.get("lesson_plan"),
            "teaching_segments": pipeline.get("teaching_segments", []),
            "frame_sequence": pipeline.get("frame_sequence", []),
            "teaching_session_state": pipeline.get("teaching_session_state"),
            "pipeline_debug": pipeline.get("pipeline_debug"),
        }
    except Exception as exc:
        logger.exception("Lesson teaching pipeline failed question=%s error=%s", trim_sentence(question, 160), exc)

    return {
        **fallback,
        "follow_up": pedagogy_follow_up(pedagogy_mode, fallback["follow_up"]),
        "suggestions": pedagogy_suggestions(pedagogy_mode, use_video_context=False),
        "timestamp": timestamps[0] if timestamps else None,
        "timestamps": timestamps[:4],
        "source": "lesson",
        "board_actions": [],
        "visual_payload": {"segments": []},
        "pipeline_debug": {"provider": "local_fallback_after_openai_pipeline_failure"},
    }


async def get_greeting_async(
    creator_name: str,
    video_title: str,
    creator_profession: str = "educator",
    lesson_context: str = "",
) -> dict[str, Any]:
    profession = creator_profession_text(creator_profession)
    lesson_summary = format_lesson_context(lesson_context, limit=2)
    greeting = await call_greeting(creator_name, profession, video_title, lesson_context)
    visual_payload = build_blackboard_visual_payload(
        title=video_title or "Lesson",
        question="What will we learn today?",
        answer=f"We are going to learn {lesson_summary}",
        focus_text=lesson_summary,
        supporting=[
            f'This class is guided by the AI clone of "{creator_name}".',
            f'Ask questions the way you would ask a real {profession}.',
        ],
    )
    fallback = heuristic_greeting_response(creator_name, profession, video_title, lesson_context)
    return {
        "greeting": greeting.get("greeting") or fallback["greeting"],
        "suggestions": greeting.get("suggestions") or fallback["suggestions"],
        "board_actions": greeting.get("board_actions") or fallback["board_actions"],
        "visual_payload": visual_payload or GREETING_VISUAL,
    }
