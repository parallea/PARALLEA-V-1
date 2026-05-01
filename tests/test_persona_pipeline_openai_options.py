from __future__ import annotations

import unittest

from backend.services.persona_pipeline import _openai_chat_options


class OpenAIChatOptionsTests(unittest.TestCase):
    def test_newer_reasoning_models_use_completion_tokens_without_temperature(self) -> None:
        for model in ["gpt-5.5", "gpt-5", "o1-mini", "o3-mini", "o4-mini"]:
            with self.subTest(model=model):
                self.assertEqual(
                    _openai_chat_options(model, max_tokens=123, temperature=0.3),
                    {"max_completion_tokens": 123},
                )

    def test_older_chat_models_keep_max_tokens_and_temperature(self) -> None:
        self.assertEqual(
            _openai_chat_options("gpt-4o", max_tokens=123, temperature=0.3),
            {"max_tokens": 123, "temperature": 0.3},
        )


if __name__ == "__main__":
    unittest.main()
