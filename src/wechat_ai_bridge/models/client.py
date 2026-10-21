"""OpenAI-compatible Chat Completions adapter for DeepSeek or approved hosts."""

import ipaddress
import base64
import io
import json
import math
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from wechat_ai_bridge.weather.planning import validate_plan


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

    def complete_vision(self, messages, *, image) -> str:
        """Attach one normalized in-memory image to the current user turn only."""
        from PIL import Image
        from wechat_ai_bridge.vision.media import VisionImage, MAX_OUTPUT_BYTES, MAX_OUTPUT_SIDE
        if (not isinstance(image, VisionImage) or image.mime != 'image/jpeg'
                or not isinstance(image.data, bytes) or not 1 <= len(image.data) <= MAX_OUTPUT_BYTES
                or not messages or messages[-1].get('role') != 'user'
                or not isinstance(messages[-1].get('content'), str)):
            raise ValueError('vision_input_invalid')
        try:
            with Image.open(io.BytesIO(image.data)) as probe:
                if (probe.format != 'JPEG' or probe.size != (image.width, image.height)
                        or min(probe.size) < 1 or max(probe.size) > MAX_OUTPUT_SIDE):
                    raise ValueError()
                probe.verify()
        except (OSError, ValueError, SyntaxError):
            raise ValueError('vision_input_invalid') from None
        prepared = [dict(item) for item in messages]
        prepared[-1]['content'] = [
            {'type': 'text', 'text': prepared[-1]['content']},
            {'type': 'image_url', 'image_url': {
                'url': 'data:image/jpeg;base64,' + base64.b64encode(image.data).decode('ascii')}}]
        return self._complete(prepared, json_mode=False)

    def complete_weather(self, messages: list[dict[str, str]]) -> dict:
        """Extract a single bounded weather plan, without JSON Output mode."""
        payload = self._payload(messages)
        payload['tools'] = [{'type': 'function', 'function': {
            'name': 'plan_weather', 'description': '只规划当前成员的天气查询；城市不明先澄清，普通闲聊不查询。',
            'parameters': {'type': 'object', 'additionalProperties': False,
                'properties': {
                    'action': {'type': 'string', 'enum': ['query', 'clarify', 'none', 'unsupported']},
                    'city': {'type': 'string', 'description': '用户明确给出的城市，可用逗号加省份或国家限定；未知填空字符串。'},
                    'start_date': {'type': 'string', 'description': '预报起始日期 YYYY-MM-DD；当前天气填空字符串。'},
                    'days': {'type': 'integer', 'minimum': 1, 'maximum': 7}},
                'required': ['action', 'city', 'start_date', 'days']}}}]
        payload['tool_choice'] = {'type': 'function', 'function': {'name': 'plan_weather'}}
        choice = self._choice(self._post(payload))
        self._check_finish(choice['finish_reason'], allow_tool_calls=True)
        message = choice.get('message')
        calls = message.get('tool_calls') if isinstance(message, dict) else None
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            raise ModelError(code='model_response_invalid')
        function = calls[0].get('function')
        if (calls[0].get('type') != 'function' or not isinstance(function, dict)
                or function.get('name') != 'plan_weather' or not isinstance(function.get('arguments'), str)):
            raise ModelError(code='model_response_invalid')
        arguments = function['arguments']
        if len(arguments.encode('utf-8')) > 64*1024:
            raise ModelError(code='model_response_too_large')
        try:
            return validate_plan(json.loads(arguments))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ModelError(code='model_response_json_invalid') from None

    def complete_decision(self, messages: list[dict[str, str]], *, meme_ids=(), voice_enabled=False) -> str:
        """Return a JSON decision from a forced tool call.

        DeepSeek's JSON Output mode can return empty content for this project's
        long persona prompt. A forced function call keeps ``reply`` structured
        without relying on that mode; an empty reply is silence, not an error.
        """
        meme_ids = tuple(meme_ids)
        if (len(set(meme_ids)) != len(meme_ids)
                or any(not isinstance(asset_id, str) or not re.fullmatch(r"[0-9]{3,6}", asset_id)
                       for asset_id in meme_ids)):
            raise ValueError("invalid meme ids")
        if type(voice_enabled) is not bool:
            raise ValueError("invalid voice flag")
        payload = self._payload(messages)
        payload["tools"] = [self._decision_tool(meme_ids, voice_enabled)]
        payload["tool_choice"] = {"type": "function", "function": {"name": "decide_reply"}}
        data = self._post(payload)
        choice = self._choice(data)
        self._check_finish(choice["finish_reason"], allow_tool_calls=True)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelError(code="model_response_invalid")
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            call = calls[0]
            function = call.get("function") if isinstance(call, dict) else None
            if (not isinstance(function, dict) or function.get("name") != "decide_reply"
                    or not isinstance(function.get("arguments"), str)):
                raise ModelError(code="model_response_invalid")
            arguments = function["arguments"]
            if len(arguments.encode("utf-8")) > 64 * 1024:
                raise ModelError(code="model_response_too_large")
            try:
                decision = json.loads(arguments)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ModelError(code="model_response_json_invalid") from exc
            if not isinstance(decision, dict):
                raise ModelError(code="model_response_invalid")
            if "reply" not in decision:
                raise ModelError(code="model_response_invalid")
            reply = decision.get("reply")
            if reply is None:
                reply = ""
            elif not isinstance(reply, str):
                raise ModelError(code="model_response_invalid")
            meme_id = decision.get("meme_id")
            if meme_id is not None and not isinstance(meme_id, str):
                raise ModelError(code="model_response_invalid")
            cleaned = {"reply": reply}
            if isinstance(meme_id, str):
                cleaned["meme_id"] = meme_id
            voice = decision.get("voice")
            if voice is not None:
                if type(voice) is not bool:
                    raise ModelError(code="model_response_invalid")
                cleaned["voice"] = voice
            return json.dumps(cleaned, ensure_ascii=False)
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            text = content.strip()
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict) and "reply" in parsed:
                return text
            return json.dumps({"reply": text}, ensure_ascii=False)
        raise ModelError(code="model_response_empty")

    @staticmethod
    def _decision_tool(meme_ids, voice_enabled=False):
        properties = {
            "reply": {"type": "string",
                      "description": "要发到群里的简短中文回复；决定不接话时填空字符串。"},
        }
        if meme_ids:
            properties["meme_id"] = {
                "type": "string", "enum": list(meme_ids),
                "description": "可选的已注册表情编号；不配图时填空字符串。",
            }
        if voice_enabled:
            properties["voice"] = {
                "type": "boolean",
                "description": "这条回复是否发成语音条；短、口语、情绪化可用语音，长说明和事实用文字。",
            }
        return {
            "type": "function",
            "function": {
                "name": "decide_reply",
                "description": "决定是否在当前群接话，并选择文字、表情或语音形式。",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["reply"],
                },
            },
        }

    def _payload(self, messages: list[dict[str, str]]) -> dict:
        payload = {"model": self.model, "messages": messages, "stream": False, "max_tokens": self.max_tokens}
        if self.thinking is not None:
            payload["thinking"] = {"type": self.thinking}
        return payload

    def _post(self, payload: dict) -> dict:
        if not self.api_key:
            raise ModelError("未配置模型 API 密钥", code="model_key_missing")
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
            return data
        except ModelError:
            raise
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

    @staticmethod
    def _choice(data: dict) -> dict:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelError(code="model_response_invalid")
        choice = choices[0]
        if "finish_reason" not in choice:
            raise ModelError(code="model_response_invalid")
        return choice

    @staticmethod
    def _check_finish(finish_reason, *, allow_tool_calls: bool = False) -> None:
        if finish_reason == "length":
            raise ModelError(code="model_response_incomplete")
        if finish_reason == "content_filter":
            raise ModelError(code="model_content_filtered")
        if finish_reason in ("aborted", "insufficient_system_resource"):
            raise ModelError(code="model_generation_interrupted")
        allowed = ("stop", "tool_calls") if allow_tool_calls else ("stop",)
        if finish_reason not in allowed:
            raise ModelError(code="model_response_invalid", retryable=False)

    def _complete(self, messages: list[dict[str, str]], *, json_mode: bool) -> str:
        payload = self._payload(messages)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(payload)
        choice = self._choice(data)
        self._check_finish(choice["finish_reason"])
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ModelError(code="model_response_invalid")
        content = message["content"]
        if not content.strip():
            raise ModelError(code="model_response_empty")
        return content.strip()
