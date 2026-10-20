import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qa.service import ChatService, DEFAULT_SYSTEM_PROMPT, _SYSTEM_RULES
from wechat_ai_bridge.settings import Settings


class RecordingModel:
    def __init__(self):
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return '{"reply":"收到。"}'


class PromptFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "persona.md"
        self.model = RecordingModel()
        self.incoming = IncomingMessage("m1", "test-group", "test-member", "大家好", False)

    def service(self):
        return ChatService(self.model, "旧人设", prompt_file=self.path)

    def test_file_replaces_persona_and_keeps_system_and_participation_rules(self):
        self.path.write_text("你是一位自然友好的群聊伙伴。", encoding="utf-8-sig")
        self.service().respond(self.incoming)
        system = self.model.calls[-1][0]["content"]
        self.assertTrue(system.startswith("你是一位自然友好的群聊伙伴。"))
        self.assertNotIn("旧人设", system)
        self.assertNotIn("\ufeff", system)
        self.assertIn(_SYSTEM_RULES, system)
        self.assertIn('{"reply":null}', system)

    def test_saved_file_is_used_by_next_request(self):
        self.path.write_text("第一版人设", encoding="utf-8")
        service = self.service()
        service.respond(self.incoming)
        self.path.write_text("第二版人设", encoding="utf-8")
        service.respond(self.incoming)
        self.assertTrue(self.model.calls[0][0]["content"].startswith("第一版人设"))
        self.assertTrue(self.model.calls[1][0]["content"].startswith("第二版人设"))
        self.assertNotIn("第一版人设", self.model.calls[1][0]["content"])

    def test_invalid_file_fails_with_fixed_error_before_model_call(self):
        service = self.service()
        for content in (b"", b" \n\t", b"\xff", b"a" * (16 * 1024 + 1)):
            with self.subTest(content_type=len(content)):
                self.path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "^人设提示词文件无效或无法读取$"):
                    service.respond(self.incoming)
        self.assertEqual(self.model.calls, [])

    def test_deleted_file_does_not_fall_back_to_previous_persona(self):
        self.path.write_text("有效人设", encoding="utf-8")
        service = self.service()
        service.respond(self.incoming)
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "^人设提示词文件无效或无法读取$"):
            service.respond(self.incoming)
        self.assertEqual(len(self.model.calls), 1)

    def test_exact_size_limit_is_accepted(self):
        self.path.write_bytes(b"a" * (16 * 1024))
        self.service().respond(self.incoming)
        self.assertTrue(self.model.calls[-1][0]["content"].startswith("a" * (16 * 1024)))

    def test_legacy_constructor_keeps_default_and_custom_prompt(self):
        for prompt in (DEFAULT_SYSTEM_PROMPT, "已有自定义人设"):
            with self.subTest(prompt=prompt):
                service = (ChatService(self.model) if prompt == DEFAULT_SYSTEM_PROMPT
                           else ChatService(self.model, prompt))
                service.respond(self.incoming)
                self.assertTrue(self.model.calls[-1][0]["content"].startswith(prompt))
                self.assertIn(_SYSTEM_RULES, self.model.calls[-1][0]["content"])


class PromptSettingsTests(unittest.TestCase):
    def test_default_prompt_file(self):
        with patch.dict(os.environ, {}, clear=True), patch("wechat_ai_bridge.settings.load_dotenv"):
            self.assertEqual(Settings.from_env().prompt_file, Path("config/persona.md"))

    def test_prompt_file_can_be_configured(self):
        with patch.dict(os.environ, {"WECHAT_AI_BRIDGE_PROMPT_FILE": "config/custom.md"}, clear=True), \
                patch("wechat_ai_bridge.settings.load_dotenv"):
            self.assertEqual(Settings.from_env().prompt_file, Path("config/custom.md"))


if __name__ == "__main__":
    unittest.main()
