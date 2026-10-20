import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_ai_bridge.models.client import ChatCompletionsClient
from wechat_ai_bridge.state.store import StateStore


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)

    def test_model_host_is_restricted(self):
        with self.assertRaises(ValueError):
            ChatCompletionsClient(base_url="http://127.0.0.1", allowed_hosts=frozenset({"127.0.0.1"}),
                                  api_key="secret", model="test")
        with self.assertRaises(ValueError):
            ChatCompletionsClient(base_url="https://other.example", allowed_hosts=frozenset({"api.deepseek.com"}),
                                  api_key="secret", model="test")

    def test_cooldown_and_hourly_quota(self):
        self.store.allow_group("group-a")
        self.assertEqual(self.store.reserve("one", "group-a", 3600, 2), "reserved")
        self.assertEqual(self.store.reserve("two", "group-a", 3600, 2), "rate_limited")
        self.assertEqual(self.store.reserve("two", "group-a", 0, 1), "rate_limited")
        self.assertEqual(self.store.reserve("one", "group-a", 0, 1), "duplicate")

    def test_admin_settings_survive_restart(self):
        self.store.allow_group("group-a")
        self.store.set_paused(True)
        other = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(other.close)
        self.assertTrue(other.is_allowed("group-a"))
        self.assertTrue(other.is_paused())

    def test_model_request_uses_chat_completions_and_rejects_truncation(self):
        class Response:
            def __init__(self, finish_reason):
                self.finish_reason = finish_reason

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                return json.dumps({"choices": [{"finish_reason": self.finish_reason,
                                                "message": {"content": "回答"}}]}).encode()[:size if size >= 0 else None]

        class Opener:
            def __init__(self, finish_reason):
                self.finish_reason = finish_reason
                self.url = None

            def open(self, request, timeout):
                self.url = request.full_url
                return Response(self.finish_reason)

        client = ChatCompletionsClient(base_url="https://api.deepseek.com",
                                       allowed_hosts=frozenset({"api.deepseek.com"}),
                                       api_key="test-key", model="deepseek-flash")
        opener = Opener("stop")
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            self.assertEqual(client.complete([{"role": "user", "content": "问题"}]), "回答")
        self.assertEqual(opener.url, "https://api.deepseek.com/chat/completions")
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=Opener("length")):
            with self.assertRaisesRegex(Exception, "模型请求失败"):
                client.complete([{"role": "user", "content": "问题"}])


if __name__ == "__main__":
    unittest.main()
