from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any

from backend import dev_reload
from backend.services import answer_service, session_manager
from config import BASE_DIR, MANIM_PUBLIC_OUTPUT_DIR, MANIM_RUNTIME_DIR


FAKE_MANIM_STEP_1 = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Step 1: numerator", font_size=34, color=WHITE)
        self.play(Write(title))
        self.wait(0.5)
"""


FAKE_MANIM_STEP_2 = """from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Step 2: denominator", font_size=34, color=WHITE)
        self.play(Write(title))
        self.wait(0.5)
"""


class MemoryRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = [dict(row) for row in rows or []]

    def all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def get(self, entity_id: str) -> dict[str, Any] | None:
        for row in self.rows:
            if row.get("id") == entity_id:
                return dict(row)
        return None

    def where(self, **kwargs) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows if all(row.get(k) == v for k, v in kwargs.items())]

    def create(self, payload: Any) -> dict[str, Any]:
        row = payload.to_dict() if hasattr(payload, "to_dict") else dict(payload)
        self.rows.append(row)
        return dict(row)

    def update(self, entity_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        for index, row in enumerate(self.rows):
            if row.get("id") == entity_id:
                row = {**row, **fields}
                self.rows[index] = row
                return dict(row)
        return None


class StudentPipelineContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_yes_after_followup_continues_and_includes_previous_answer(self) -> None:
        student = {"id": "usr_test", "name": "Test Student"}
        persona = {
            "id": "per_test",
            "teacher_id": "usr_teacher",
            "teacher_name": "Demo Teacher",
            "profession": "Math teacher",
            "active_persona_prompt": "Teach patiently and advance one small step at a time.",
            "detected_topics": [],
        }
        sessions = MemoryRepo()
        messages = MemoryRepo()
        users = MemoryRepo([student])
        personas = MemoryRepo([persona])
        empty = MemoryRepo()
        prompts: list[str] = []
        render_payloads: list[dict[str, Any]] = []

        async def fake_llm_json(task: str, system_prompt: str, user_prompt: str, **_: Any) -> dict[str, Any]:
            self.assertEqual(task, "teaching_pipeline")
            self.assertIn("spoken_answer", system_prompt)
            prompts.append(user_prompt)
            if len(prompts) == 1:
                return {
                    "spoken_answer": "Step 1: A numerator tells how many parts we are taking.",
                    "teaching_state_update": {
                        "current_topic": "fractions",
                        "current_step": "step 1 numerator",
                        "student_understanding_summary": "Student has started fractions.",
                        "unresolved_student_question": "",
                        "next_teaching_goal": "Explain the denominator as the size of the whole.",
                    },
                    "visual_plan_with_timestamps": [
                        {"id": "vis_1", "start": 0.0, "end": 3.0, "matches_spoken_text": "numerator", "description": "Highlight selected parts."}
                    ],
                    "manim_code": FAKE_MANIM_STEP_1,
                    "follow_up_question": "Shall we continue?",
                }
            self.assertIn("You previously gave this answer to the student:", user_prompt)
            self.assertIn("Step 1: A numerator tells how many parts we are taking.", user_prompt)
            self.assertIn("The student has now replied:\nyes", user_prompt)
            self.assertIn("Continue from the previous answer. Do not restart.", user_prompt)
            return {
                "spoken_answer": "Step 2: The denominator tells how many equal parts make the whole.",
                "teaching_state_update": {
                    "current_topic": "fractions",
                    "current_step": "step 2 denominator",
                    "student_understanding_summary": "Student is ready for the second fraction step.",
                    "unresolved_student_question": "",
                    "next_teaching_goal": "Give a concrete fraction example.",
                },
                "visual_plan_with_timestamps": [
                    {"id": "vis_1", "start": 0.0, "end": 3.0, "matches_spoken_text": "denominator", "description": "Show the whole split into equal parts."}
                ],
                "manim_code": FAKE_MANIM_STEP_2,
                "follow_up_question": "Want an example?",
            }

        async def fake_render_manim_payload_async(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
            render_payloads.append(payload)
            return {
                "video_url": "/rendered-scenes/manim/test.mp4",
                "media_url": "/rendered-scenes/manim/test.mp4",
                "public_url": "/rendered-scenes/manim/test.mp4",
                "cache_hit": False,
                "used_fallback": False,
                "payload": {"manim_code_validation": {"valid": True, "fallback_used": False}},
            }

        with (
            patch.object(session_manager, "sessions_repo", sessions),
            patch.object(session_manager, "messages_repo", messages),
            patch.object(session_manager, "users_repo", users),
            patch.object(session_manager, "personas_repo", personas),
            patch.object(session_manager, "missing_topics_repo", empty),
            patch.object(session_manager, "roadmaps_repo", empty),
            patch.object(session_manager, "roadmap_parts_repo", empty),
            patch.object(session_manager, "videos_repo", empty),
            patch.object(session_manager, "match_student_topic_to_roadmaps", return_value={
                "topicExists": False,
                "mode": "persona_only",
                "matchedRoadmapId": None,
                "matchedPartIds": [],
                "confidence": 0.0,
                "studentTopic": "fractions",
            }),
            patch.object(answer_service, "llm_json", fake_llm_json),
            patch.object(session_manager, "render_manim_payload_async", fake_render_manim_payload_async),
        ):
            created = session_manager.create_session(student=student, persona=persona)
            session_id = created["session"]["id"]
            await session_manager.send_message(session_id, "I want to learn fractions")
            first = await session_manager.send_message(session_id, "yes")
            second = await session_manager.send_message(session_id, "yes")

        self.assertIn("Step 1", first["message"]["content"])
        self.assertIn("Step 2", second["message"]["content"])
        self.assertNotIn("A numerator tells", second["message"]["content"])
        self.assertGreaterEqual(len(prompts), 2)
        self.assertEqual(render_payloads[0]["manim_code"].strip(), FAKE_MANIM_STEP_1.strip())
        self.assertEqual(render_payloads[1]["manim_code"].strip(), FAKE_MANIM_STEP_2.strip())
        persisted_session = sessions.get(session_id) or {}
        memory = persisted_session.get("memory") or {}
        self.assertEqual(memory.get("current_step"), "step 2 denominator")
        self.assertIn("Step 2", memory.get("last_assistant_answer", ""))
        self.assertGreaterEqual(len(memory.get("recent_turns") or []), 6)

    async def test_topic_match_starts_matched_middle_part_not_first(self) -> None:
        student = {"id": "usr_test", "name": "Test Student"}
        persona = {"id": "per_test", "teacher_name": "Demo Teacher", "active_persona_prompt": "Teach clearly."}
        video = {"id": "vid_test", "persona_id": "per_test", "title": "Linear algebra lesson", "status": "ready"}
        roadmap = {"id": "rmp_test", "video_id": "vid_test", "persona_id": "per_test", "title": "ML math"}
        parts = MemoryRepo([
            {"id": "prt_1", "roadmap_id": "rmp_test", "order": 0, "title": "Intro", "start_time": 0.0, "end_time": 70.0},
            {"id": "prt_2", "roadmap_id": "rmp_test", "order": 1, "title": "Matrix operations", "start_time": 70.0, "end_time": 140.0},
            {"id": "prt_3", "roadmap_id": "rmp_test", "order": 2, "title": "Neural nets", "start_time": 140.0, "end_time": 210.0},
        ])
        sessions = MemoryRepo()
        messages = MemoryRepo()
        routing = {
            "topicExists": True,
            "mode": "video_context",
            "matchedRoadmapId": "rmp_test",
            "matchedVideoId": "vid_test",
            "matchedPartId": "prt_2",
            "matchedPartIds": ["prt_2", "prt_3"],
            "matchedPartTitle": "Matrix operations",
            "confidence": 0.91,
            "start_time": 70.0,
            "end_time": 140.0,
            "studentTopic": "matrix operations",
            "matchReason": "phrase match in part title",
        }

        with (
            patch.object(session_manager, "sessions_repo", sessions),
            patch.object(session_manager, "messages_repo", messages),
            patch.object(session_manager, "users_repo", MemoryRepo([student])),
            patch.object(session_manager, "personas_repo", MemoryRepo([persona])),
            patch.object(session_manager, "missing_topics_repo", MemoryRepo()),
            patch.object(session_manager, "roadmaps_repo", MemoryRepo([roadmap])),
            patch.object(session_manager, "roadmap_parts_repo", parts),
            patch.object(session_manager, "videos_repo", MemoryRepo([video])),
            patch.object(session_manager, "match_student_topic_to_roadmaps", return_value=routing),
        ):
            created = session_manager.create_session(student=student, persona=persona)
            env = await session_manager.set_topic(created["session"]["id"], "matrix operations")

        self.assertEqual(env["promptFor"], "video_part")
        self.assertEqual(env["currentPart"]["id"], "prt_2")
        self.assertEqual(env["currentPart"]["start_time"], 70.0)
        self.assertEqual(env["currentVideo"]["id"], "vid_test")
        self.assertEqual(env["currentVideo"]["start_time"], 70.0)
        persisted = sessions.get(created["session"]["id"]) or {}
        self.assertEqual(persisted.get("current_part_id"), "prt_2")
        self.assertEqual(persisted.get("current_video_id"), "vid_test")

    async def test_continue_uses_next_consecutive_part_not_first(self) -> None:
        student = {"id": "usr_test", "name": "Test Student"}
        persona = {"id": "per_test", "teacher_name": "Demo Teacher", "active_persona_prompt": "Teach clearly."}
        video = {"id": "vid_test", "persona_id": "per_test", "title": "Linear algebra lesson", "status": "ready"}
        roadmap = {"id": "rmp_test", "video_id": "vid_test", "persona_id": "per_test", "title": "ML math"}
        session = {
            "id": "ses_test",
            "student_id": "usr_test",
            "persona_id": "per_test",
            "selected_topic": "matrix operations",
            "mode": "video_context",
            "state": "awaiting_part_feedback",
            "current_roadmap_id": "rmp_test",
            "current_video_id": "vid_test",
            "current_part_id": "prt_2",
            "current_part_index": 1,
            "matched_part_ids": ["prt_2", "prt_3"],
            "memory": {},
        }
        parts = MemoryRepo([
            {"id": "prt_1", "roadmap_id": "rmp_test", "order": 0, "title": "Intro", "start_time": 0.0, "end_time": 70.0},
            {"id": "prt_2", "roadmap_id": "rmp_test", "order": 1, "title": "Matrix operations", "start_time": 70.0, "end_time": 140.0},
            {"id": "prt_3", "roadmap_id": "rmp_test", "order": 2, "title": "Neural nets", "start_time": 140.0, "end_time": 210.0},
        ])

        with (
            patch.object(session_manager, "sessions_repo", MemoryRepo([session])),
            patch.object(session_manager, "messages_repo", MemoryRepo()),
            patch.object(session_manager, "users_repo", MemoryRepo([student])),
            patch.object(session_manager, "personas_repo", MemoryRepo([persona])),
            patch.object(session_manager, "roadmaps_repo", MemoryRepo([roadmap])),
            patch.object(session_manager, "roadmap_parts_repo", parts),
            patch.object(session_manager, "videos_repo", MemoryRepo([video])),
        ):
            env = await session_manager.send_message("ses_test", "continue")

        self.assertEqual(env["promptFor"], "video_part")
        self.assertEqual(env["currentPart"]["id"], "prt_3")
        self.assertEqual(env["currentPart"]["start_time"], 140.0)
        self.assertEqual(env["session"]["state"], "playing_video_part")

    async def test_last_part_continue_switches_to_persona_continuation(self) -> None:
        student = {"id": "usr_test", "name": "Test Student"}
        persona = {
            "id": "per_test",
            "teacher_name": "Demo Teacher",
            "profession": "Math teacher",
            "active_persona_prompt": "Teach clearly.",
            "detected_topics": ["Linear algebra applications"],
        }
        video = {"id": "vid_test", "persona_id": "per_test", "title": "Linear algebra lesson", "status": "ready"}
        roadmap = {"id": "rmp_test", "video_id": "vid_test", "persona_id": "per_test", "title": "ML math", "topics": ["Linear algebra applications"]}
        session = {
            "id": "ses_test",
            "student_id": "usr_test",
            "persona_id": "per_test",
            "selected_topic": "svd",
            "mode": "video_context",
            "state": "awaiting_part_feedback",
            "current_roadmap_id": "rmp_test",
            "current_video_id": "vid_test",
            "current_part_id": "prt_2",
            "current_part_index": 1,
            "memory": {},
        }
        parts = MemoryRepo([
            {"id": "prt_1", "roadmap_id": "rmp_test", "order": 0, "title": "Intro", "start_time": 0.0, "end_time": 70.0},
            {"id": "prt_2", "roadmap_id": "rmp_test", "order": 1, "title": "SVD wrap-up", "summary": "The final uploaded part.", "start_time": 70.0, "end_time": 140.0},
        ])

        async def fake_generate(**_: Any) -> dict[str, Any]:
            return {
                "speech": {"text": "Now we extend the lesson with a new visual example.", "segments": []},
                "visual": {"visualNeeded": True, "visualType": "manim", "manimCode": FAKE_MANIM_STEP_1},
                "teachingControl": {"askFollowUp": "Want to keep going?"},
            }

        async def fake_render(*_: Any, **__: Any) -> dict[str, Any]:
            return {"type": "manim", "status": "ready", "renderStatus": "ready", "videoUrl": "/rendered-scenes/manim/next.mp4"}

        sessions = MemoryRepo([session])
        with (
            patch.object(session_manager, "sessions_repo", sessions),
            patch.object(session_manager, "messages_repo", MemoryRepo()),
            patch.object(session_manager, "users_repo", MemoryRepo([student])),
            patch.object(session_manager, "personas_repo", MemoryRepo([persona])),
            patch.object(session_manager, "roadmaps_repo", MemoryRepo([roadmap])),
            patch.object(session_manager, "roadmap_parts_repo", parts),
            patch.object(session_manager, "videos_repo", MemoryRepo([video])),
            patch.object(session_manager, "generate_teaching_response_with_visuals", fake_generate),
            patch.object(session_manager, "_render_teaching_visual", fake_render),
        ):
            env = await session_manager.send_message("ses_test", "continue")

        self.assertEqual(env["session"]["state"], "persona_only_teaching")
        self.assertEqual(env["session"]["mode"], "persona_continuation_after_video")
        self.assertIn("last uploaded video part", env["message"]["content"])
        self.assertEqual(env["visual"]["type"], "manim")
        persisted = sessions.get("ses_test") or {}
        self.assertTrue(persisted.get("last_part_was_final"))
        self.assertTrue(persisted.get("next_suggested_topic"))

    def test_generated_runtime_paths_are_reload_excluded(self) -> None:
        self.assertTrue(dev_reload.is_generated_runtime_path(MANIM_RUNTIME_DIR / "scenes" / "generated.py"))
        self.assertTrue(dev_reload.is_generated_runtime_path(MANIM_PUBLIC_OUTPUT_DIR / "generated.py"))
        self.assertTrue(dev_reload.is_generated_runtime_path(BASE_DIR / "data" / "renders" / "manim" / "generated.py"))
        self.assertIn("data/renders/*", dev_reload.UVICORN_RELOAD_EXCLUDES)
        self.assertIn("manim_runtime/*", dev_reload.UVICORN_RELOAD_EXCLUDES)
        self.assertIn("*.mp4", dev_reload.UVICORN_RELOAD_EXCLUDES)

    def test_original_teacher_video_public_contract_is_preserved(self) -> None:
        public = session_manager._public_video(
            {
                "id": "vid_test",
                "title": "Original lesson",
                "duration": 42.0,
                "thumbnail_url": "/thumbnails/vid_test.jpg",
            }
        )
        self.assertEqual(public["stream_url"], "/api/student/videos/vid_test/stream")
        self.assertEqual(public["thumbnail_url"], "/thumbnails/vid_test.jpg")
        self.assertEqual(public["title"], "Original lesson")


if __name__ == "__main__":
    unittest.main()
