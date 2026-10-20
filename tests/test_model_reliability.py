import io
import json
import os
import unittest
from http.client import BadStatusLine, IncompleteRead, LineTooLong, RemoteDisconnected
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from wechat_ai_bridge.models.client import ChatCompletionsClient, ModelError
from wechat_ai_bridge.settings import Settings


class ModelReliabilityTests(unittest.TestCase):
    def client(self, **kwargs):
        return ChatCompletionsClient(
            base_url="https://api.deepseek.com", allowed_hosts=frozenset({"api.deepseek.com"}),
            api_key="fixture-secret", model="fixture-model", **kwargs,
        )

    def response(self, body):
        response = io.BytesIO(body)
        opener = Mock()
        opener.open.return_value = response
        return opener

    def complete_body(self, body):
        opener = self.response(body)
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            return self.client().complete([{"role": "user", "content": "fixture-prompt"}])

    def assert_body_error(self, body, code, retryable):
        with self.assertRaises(ModelError) as caught:
            self.complete_body(body)
        self.assertEqual(caught.exception.code, code)
        self.assertIs(caught.exception.retryable, retryable)
        self.assertNotIn("fixture", str(caught.exception))

    def test_empty_content_is_distinct_and_retryable(self):
        for content in ("", " \n\t "):
            with self.subTest(content=content):
                self.assert_body_error(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode(), "model_response_empty", True)

    def test_broken_or_empty_json_is_distinct_and_retryable(self):
        for body in (b"", b"{fixture", b"\xff"):
            with self.subTest(body=body):
                self.assert_body_error(body, "model_response_json_invalid", True)

    def test_invalid_containers_and_content_are_retryable(self):
        for data in (None, [], 1, "fixture", {}, {"choices": []}, {"choices": {}},
                     {"choices": [None]}, {"choices": [{"finish_reason": "stop", "message": None}]},
                     {"choices": [{"finish_reason": "stop", "message": {"content": 1}}]},
                     {"choices": [{"message": {"content": "fixture"}}]}):
            with self.subTest(data=data):
                self.assert_body_error(json.dumps(data).encode(), "model_response_invalid", True)

    def test_finish_reason_classification_never_returns_partial_content(self):
        for reason, code, retryable in (
            ("length", "model_response_incomplete", True),
            ("content_filter", "model_content_filtered", False),
            ("aborted", "model_generation_interrupted", True),
            ("insufficient_system_resource", "model_generation_interrupted", True),
            ("tool_calls", "model_response_invalid", False),
            (None, "model_response_invalid", False),
            ({"fixture": "malformed"}, "model_response_invalid", False),
        ):
            with self.subTest(reason=reason):
                self.assert_body_error(json.dumps({"choices": [{"finish_reason": reason, "message": {"content": "fixture partial"}}]}).encode(), code, retryable)

    def test_deeply_nested_json_becomes_safe_model_error(self):
        self.assert_body_error(b"[" * 2000 + b"]" * 2000, "model_response_json_invalid", True)

    def test_oversized_json_integer_becomes_safe_model_error(self):
        self.assert_body_error(b"9" * 5000, "model_response_json_invalid", True)

    def test_response_at_limit_is_accepted(self):
        body = b'{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}'
        self.assertEqual(self.complete_body(body + b" " * (1024 * 1024 - len(body))), "ok")

    def test_interrupted_body_read_is_retryable_network_error(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b"fixture-private", 100)
        opener = Mock()
        opener.open.return_value = response
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            with self.assertRaises(ModelError) as caught:
                self.client().complete([])
        self.assertEqual(caught.exception.code, "model_network_error")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("fixture", str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_bad_status_line_is_retryable_network_error(self):
        opener = Mock()
        opener.open.side_effect = BadStatusLine("fixture-private-status")
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            with self.assertRaises(ModelError) as caught:
                self.client().complete([])
        self.assertEqual(caught.exception.code, "model_network_error")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("fixture", str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_overlong_http_line_is_retryable_network_error(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = LineTooLong("fixture-private-header")
        opener = Mock()
        opener.open.return_value = response
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            with self.assertRaises(ModelError) as caught:
                self.client().complete([])
        self.assertEqual(caught.exception.code, "model_network_error")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("fixture", str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_response_is_bounded_to_one_mebibyte(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = lambda size: b"x" * size
        opener = Mock()
        opener.open.return_value = response
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            with self.assertRaises(ModelError) as caught:
                self.client().complete([])
        self.assertEqual(caught.exception.code, "model_response_too_large")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(sum(call.args[0] for call in response.read.call_args_list), 1024 * 1024 + 1)

    def test_json_whitespace_is_valid_and_default_payload_has_larger_budget(self):
        body = b' \n {"choices":[{"finish_reason":"stop","message":{"content":" fixture reply "}}]} \t'
        opener = self.response(body)
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener) as build:
            self.assertEqual(self.client().complete([]), "fixture reply")
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 45)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)["max_tokens"], 1200)
        proxies = [arg for arg in build.call_args.args if isinstance(arg, ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})

    def test_configured_timeout_and_max_tokens_are_used(self):
        opener = self.response(b'{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}')
        with patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
            self.client(timeout_seconds=60.5, max_tokens=2048).complete([])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 60.5)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)["max_tokens"], 2048)

    def test_invalid_constructor_limits_raise_value_error(self):
        for name, values in (("timeout_seconds", (True, 0, 121, float("nan"), float("inf"), "secret", 10 ** 1000)),
                             ("max_tokens", (True, 127, 8193, 128.5, "secret"))):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.client(**{name: value})

    def test_http_errors_have_fixed_safe_codes_and_explicit_retry_eligibility(self):
        for status in (301, 400, 401, 403, 404, 408, 413, 422, 429, 500, 501, 502, 503, 504):
            opener = Mock()
            opener.open.side_effect = HTTPError("https://fixture-secret", status, "fixture-private-body", {}, None)
            with self.subTest(status=status), patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
                with self.assertRaises(ModelError) as caught:
                    self.client().complete([])
                self.assertEqual(caught.exception.code, f"model_http_{status}")
                self.assertEqual(caught.exception.retryable, status in (408, 429, 500, 502, 503, 504))
                self.assertNotIn("fixture", str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)

    def test_network_errors_are_retryable_and_do_not_retry_inside_client(self):
        for error, code in ((TimeoutError("fixture"), "model_timeout"),
                            (URLError(TimeoutError("fixture")), "model_timeout"),
                            (URLError("fixture"), "model_network_error"),
                            (RemoteDisconnected("fixture"), "model_network_error"),
                            (OSError("fixture"), "model_network_error")):
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(code=code), patch("wechat_ai_bridge.models.client.build_opener", return_value=opener):
                with self.assertRaises(ModelError) as caught:
                    self.client().complete([])
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(caught.exception.retryable)
                self.assertNotIn("fixture", str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)

    def test_generic_and_missing_key_errors_are_not_retryable(self):
        self.assertFalse(ModelError().retryable)
        self.assertFalse(ModelError(code="fixture-secret").retryable)
        client = self.client()
        client.api_key = ""
        with self.assertRaises(ModelError) as caught:
            client.complete([])
        self.assertEqual(caught.exception.code, "model_key_missing")
        self.assertFalse(caught.exception.retryable)


class ModelReliabilitySettingsTests(unittest.TestCase):
    def settings(self, values):
        with patch.dict(os.environ, values, clear=True), patch("wechat_ai_bridge.settings.load_dotenv"):
            return Settings.from_env()

    def test_defaults(self):
        settings = self.settings({})
        self.assertEqual(settings.model_timeout_seconds, 45)
        self.assertEqual(settings.model_max_tokens, 1200)

    def test_environment_overrides(self):
        settings = self.settings({"WECHAT_AI_BRIDGE_MODEL_TIMEOUT_SECONDS": "60.5", "WECHAT_AI_BRIDGE_MODEL_MAX_TOKENS": "2048"})
        self.assertEqual(settings.model_timeout_seconds, 60.5)
        self.assertEqual(settings.model_max_tokens, 2048)

    def test_invalid_environment_values_are_safe_errors(self):
        for name, values in (("TIMEOUT_SECONDS", ("fixture-secret", "nan", "inf", "0", "121", "")),
                             ("MAX_TOKENS", ("fixture-secret", "nan", "128.5", "127", "8193", ""))):
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError) as caught:
                        self.settings({f"WECHAT_AI_BRIDGE_MODEL_{name}": value})
                    self.assertNotIn("fixture", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
