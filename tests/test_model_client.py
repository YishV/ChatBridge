import json
import unittest
from unittest.mock import Mock, patch

from wechat_ai_bridge.models.client import ChatCompletionsClient, ModelError


class ModelClientTests(unittest.TestCase):
    def client(self):
        return ChatCompletionsClient(base_url="https://api.deepseek.com", allowed_hosts=frozenset({"api.deepseek.com"}),
            api_key="fixture", model="deepseek-flash", thinking="disabled")

    def opener(self, payload):
        response = Mock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        return opener

    def test_malformed_response_containers_are_model_errors(self):
        for payload in (None, [], {"choices": None}, {"choices": [None]}, {"choices": [{"message": None, "finish_reason": "stop"}]}):
            with self.subTest(payload=payload), patch("wechat_ai_bridge.models.client.build_opener", return_value=self.opener(payload)):
                with self.assertRaises(ModelError):
                    self.client().complete([{"role": "user", "content": "hi"}])

    def test_json_mode_and_thinking_disabled_are_sent_only_when_requested(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": '{"reply":null}'}}]}
        opener = self.opener(payload)
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            self.client().complete_json([{"role": "user", "content": "Return JSON"}])
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
