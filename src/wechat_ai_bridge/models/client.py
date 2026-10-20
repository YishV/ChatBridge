"""OpenAI-compatible Chat Completions adapter for DeepSeek or approved hosts."""

import ipaddress
import json
import math
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_MAX_RESPONSE_BYTES = 1024 * 1024


class ModelError(Exception):
    """A model call failed without exposing credentials or prompt content."""

    def __init__(self, message="模型请求失败或响应无效", *, code="model_request_failed", retryable=None):
        super().__init__(message)
        transient = {"model_timeout", "model_network_error", "model_response_incomplete",
                     "model_response_json_invalid", "model_response_empty", "model_response_invalid",
                     "model_generation_interrupted"}
        allowed = transient | {"model_key_missing", "model_request_failed", "model_response_too_large",
                               "model_content_filtered"}
        self.code = (code if isinstance(code, str) and (
            code in allowed or re.fullmatch(r"model_http_[1-5][0-9]{2}", code)
        ) else "model_request_failed")
        inferred = self.code in transient or self.code in {
            "model_http_408", "model_http_429", "model_http_500", "model_http_502",
            "model_http_503", "model_http_504",
        }
        self.retryable = inferred if retryable is None else bool(retryable)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsClient:
    def __init__(self, *, base_url: str, allowed_hosts: frozenset[str], api_key: str, model: str,
                 thinking: str | None = None, timeout_seconds: float = 45, max_tokens: int = 1200):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not 1 <= timeout_seconds <= 120 or not math.isfinite(timeout_seconds)):
            raise ValueError("模型请求超时必须为 1 至 120 秒")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 128 <= max_tokens <= 8192:
            raise ValueError("模型输出上限必须为 128 至 8192 个 token")
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型地址必须是 HTTPS 基础地址")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError("模型地址不能使用内网或本地 IP")
        except ValueError as exc:
            if "模型地址" in str(exc):
                raise
        if host not in allowed_hosts:
            raise ValueError("模型地址不在允许的主机名单中")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        if thinking not in (None, "enabled", "disabled"):
            raise ValueError("无效的思考模式")
        self.thinking = thinking if host == "api.deepseek.com" else None

    def complete(self, messages: list[dict[str, str]]) -> str:
        return self._complete(messages, json_mode=False)

    def complete_json(self, messages: list[dict[str, str]]) -> str:
        return self._complete(messages, json_mode=True)

    def _complete(self, messages: list[dict[str, str]], *, json_mode: bool) -> str:
        if not self.api_key:
            raise ModelError("未配置模型 API 密钥", code="model_key_missing")
        payload = {"model": self.model, "messages": messages, "stream": False, "max_tokens": self.max_tokens}
        if self.thinking is not None:
            payload["thinking"] = {"type": self.thinking}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode()
        request = Request(self.url, body, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }, method="POST")
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw_body) > _MAX_RESPONSE_BYTES:
                raise ModelError(code="model_response_too_large")
            try:
                data = json.loads(raw_body)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ModelError(code="model_response_json_invalid") from exc
            if not isinstance(data, dict):
                raise ModelError(code="model_response_invalid")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ModelError(code="model_response_invalid")
            choice = choices[0]
            if "finish_reason" not in choice:
                raise ModelError(code="model_response_invalid")
            finish_reason = choice["finish_reason"]
            if finish_reason == "length":
                raise ModelError(code="model_response_incomplete")
            if finish_reason == "content_filter":
                raise ModelError(code="model_content_filtered")
            if finish_reason in ("aborted", "insufficient_system_resource"):
                raise ModelError(code="model_generation_interrupted")
            if finish_reason != "stop":
                raise ModelError(code="model_response_invalid", retryable=False)
            message = choice.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ModelError(code="model_response_invalid")
            content = message["content"]
            if not content.strip():
                raise ModelError(code="model_response_empty")
            return content.strip()
        except HTTPError as exc:
            raise ModelError(code=f"model_http_{exc.code}") from exc
        except TimeoutError as exc:
            raise ModelError(code="model_timeout") from exc
        except URLError as exc:
            code = "model_timeout" if isinstance(exc.reason, TimeoutError) else "model_network_error"
            raise ModelError(code=code) from exc
        except (OSError, HTTPException) as exc:
            raise ModelError(code="model_network_error") from exc
        except (KeyError, IndexError, TypeError, ValueError, RecursionError) as exc:
            raise ModelError(code="model_response_invalid") from exc
