import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from chat_bridge.models.client import ChatCompletionsClient, ModelError


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
            with self.subTest(payload=payload), patch("chat_bridge.models.client.build_opener", return_value=self.opener(payload)):
                with self.assertRaises(ModelError):
                    self.client().complete([{"role": "user", "content": "hi"}])

    def test_json_mode_and_thinking_disabled_are_sent_only_when_requested(self):
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": '{"reply":null}'}}]}
        opener = self.opener(payload)
        with patch("chat_bridge.models.client.build_opener", return_value=opener):
            self.client().complete_json([{"role": "user", "content": "Return JSON"}])
        body = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_failure_codes_distinguish_http_timeout_and_incomplete_output(self):
        for error, expected in [(HTTPError('https://api.deepseek.com', 429, 'private body', {}, None), 'model_http_429'),
                                (TimeoutError('private body'), 'model_timeout')]:
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(code=expected), patch('chat_bridge.models.client.build_opener', return_value=opener):
                with self.assertRaises(ModelError) as caught:
                    self.client().complete([{'role': 'user', 'content': 'private message'}])
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn('private', caught.exception.code)
        payload = {'choices': [{'finish_reason': 'length', 'message': {'content': 'private content'}}]}
        with patch('chat_bridge.models.client.build_opener', return_value=self.opener(payload)):
            with self.assertRaises(ModelError) as caught:
                self.client().complete([{'role': 'user', 'content': 'private message'}])
            self.assertEqual(caught.exception.code, 'model_response_incomplete')


if __name__ == "__main__":
    unittest.main()
