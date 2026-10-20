"""OpenAI-compatible Chat Completions adapter for DeepSeek or approved hosts."""

import ipaddress
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelError(Exception):
    """A model call failed without exposing credentials or prompt content."""

    def __init__(self, message="模型请求失败或响应无效", *, code="model_request_failed"):
        super().__init__(message)
        allowed = {"model_key_missing", "model_timeout", "model_network_error", "model_response_incomplete",
                   "model_response_invalid", "model_request_failed"}
        self.code = code if code in allowed or re.fullmatch(r"model_http_[1-5][0-9]{2}", code) else "model_request_failed"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsClient:
    def __init__(self, *, base_url: str, allowed_hosts: frozenset[str], api_key: str, model: str,
                 thinking: str | None = None):
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
        payload = {"model": self.model, "messages": messages, "stream": False, "max_tokens": 500}
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
            with build_opener(_NoRedirect()).open(request, timeout=20) as response:
                data = json.load(response)
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ModelError(code="model_response_incomplete")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model response")
            return content.strip()
        except HTTPError as exc:
            raise ModelError(code=f"model_http_{exc.code}") from exc
        except TimeoutError as exc:
            raise ModelError(code="model_timeout") from exc
        except URLError as exc:
            code = "model_timeout" if isinstance(exc.reason, TimeoutError) else "model_network_error"
            raise ModelError(code=code) from exc
        except OSError as exc:
            raise ModelError(code="model_network_error") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError(code="model_response_invalid") from exc
