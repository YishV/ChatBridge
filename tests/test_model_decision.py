import io
import json
import unittest
from unittest.mock import Mock, patch

from chat_bridge.models.client import ChatCompletionsClient, ModelError


class DecisionClientTests(unittest.TestCase):
    def client(self):
        return ChatCompletionsClient(
            base_url="https://api.deepseek.com", allowed_hosts=frozenset({"api.deepseek.com"}),
            api_key="fixture-secret", model="deepseek-flash",
        )

    def response(self, body):
        opener = Mock()
        opener.open.return_value = io.BytesIO(body)
        return opener

    def call(self, body, *, meme_ids=()):
        opener = self.response(body)
        with patch("chat_bridge.models.client.build_opener", return_value=opener):
            result = self.client().complete_decision([{"role": "user", "content": "fixture"}],
                                                     meme_ids=meme_ids)
        return result, opener

    @staticmethod
    def tool_body(arguments, *, finish="tool_calls"):
        return json.dumps({"choices": [{"finish_reason": finish, "message": {
            "content": "",
            "tool_calls": [{"type": "function", "function": {
                "name": "decide_reply", "arguments": arguments}}],
        }}]}).encode()

    def test_forced_tool_call_returns_decision_and_does_not_use_json_mode(self):
        result, opener = self.call(self.tool_body('{"reply":"你好","meme_id":"083"}'),
                                   meme_ids=("083", "087"))
        self.assertEqual(json.loads(result), {"reply": "你好", "meme_id": "083"})
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertNotIn("response_format", payload)
        self.assertEqual(payload["tool_choice"],
                         {"type": "function", "function": {"name": "decide_reply"}})
        properties = payload["tools"][0]["function"]["parameters"]["properties"]
        self.assertEqual(properties["meme_id"]["enum"], ["083", "087"])

    def test_empty_reply_is_a_valid_silence_decision(self):
        result, _ = self.call(self.tool_body('{"reply":""}'))
        self.assertEqual(json.loads(result), {"reply": ""})

    def test_null_reply_is_normalized_to_silence(self):
        result, _ = self.call(self.tool_body('{"reply":null}'))
        self.assertEqual(json.loads(result), {"reply": ""})

    def test_extra_tool_arguments_are_ignored(self):
        result, _ = self.call(self.tool_body('{"reply":"好","meme_id":"083","note":"ignored"}'))
        self.assertEqual(json.loads(result), {"reply": "好", "meme_id": "083"})

    def test_plain_json_null_reply_fallback_is_preserved(self):
        body = json.dumps({"choices": [{"finish_reason": "stop",
                                        "message": {"content": '{"reply":null}'}}]}).encode()
        result, _ = self.call(body)
        self.assertEqual(result, '{"reply":null}')

    def test_plain_content_fallback_is_wrapped_as_a_reply(self):
        body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": " 直接回答 "}}]}).encode()
        result, _ = self.call(body)
        self.assertEqual(json.loads(result), {"reply": "直接回答"})

    def test_empty_content_without_tool_call_is_retryable(self):
        body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "  "}}]}).encode()
        with self.assertRaises(ModelError) as caught:
            self.call(body)
        self.assertEqual(caught.exception.code, "model_response_empty")
        self.assertTrue(caught.exception.retryable)

    def test_malformed_arguments_are_a_retryable_json_error(self):
        with self.assertRaises(ModelError) as caught:
            self.call(self.tool_body('{"reply":'))
        self.assertEqual(caught.exception.code, "model_response_json_invalid")
        self.assertTrue(caught.exception.retryable)

    def test_wrong_tool_name_is_invalid(self):
        body = json.dumps({"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "",
            "tool_calls": [{"type": "function", "function": {
                "name": "other", "arguments": '{"reply":"x"}'}}],
        }}]}).encode()
        with self.assertRaises(ModelError) as caught:
            self.call(body)
        self.assertEqual(caught.exception.code, "model_response_invalid")

    def test_length_tool_call_is_incomplete(self):
        with self.assertRaises(ModelError) as caught:
            self.call(self.tool_body('{"reply":"x"}', finish="length"))
        self.assertEqual(caught.exception.code, "model_response_incomplete")


if __name__ == "__main__":
    unittest.main()
