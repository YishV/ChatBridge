"""Minimal OneBot 11 HTTP client used by the QQ adapter."""

import json
import urllib.error
import urllib.request
from pathlib import Path

from wechat_ai_bridge.qq.config import QQSettings

_MAX_RESPONSE_BYTES = 1024 * 1024


class OneBotError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class OneBotClient:
    def __init__(self, settings: QQSettings):
        self.settings = settings
        self.base = settings.api_url.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def call(self, action: str, params: dict):
        if not isinstance(action, str) or not action or len(action) > 64:
            raise OneBotError('onebot_action_invalid')
        body = json.dumps({'action': action, 'params': params}, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.settings.access_token:
            headers['Authorization'] = 'Bearer ' + self.settings.access_token
        request = urllib.request.Request(self.base + '/', data=body, headers=headers, method='POST')
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise OneBotError('onebot_response_too_large')
        except OneBotError:
            raise
        except urllib.error.HTTPError as exc:
            raise OneBotError('onebot_http_' + str(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OneBotError('onebot_unavailable') from exc
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise OneBotError('onebot_response_invalid') from exc
        if not isinstance(data, dict):
            raise OneBotError('onebot_response_invalid')
        if data.get('status') != 'ok' or data.get('retcode') not in (0, None):
            raise OneBotError('onebot_action_failed')
        return data

    def login_info(self) -> dict:
        data = self.call('get_login_info', {})
        result = data.get('data')
        if not isinstance(result, dict):
            raise OneBotError('onebot_response_invalid')
        return result

    def send_group_message(self, group_id: str, message: list[dict]) -> str:
        if not isinstance(group_id, str) or not group_id or len(group_id) > 64:
            raise OneBotError('onebot_group_invalid')
        if not isinstance(message, list) or not message:
            raise OneBotError('onebot_message_invalid')
        numeric = int(group_id) if group_id.isdigit() else group_id
        data = self.call('send_group_msg', {'group_id': numeric, 'message': message, 'auto_escape': False})
        result = data.get('data')
        message_id = result.get('message_id') if isinstance(result, dict) else None
        if message_id is None or isinstance(message_id, bool):
            raise OneBotError('onebot_receipt_missing')
        return str(message_id)

    def send_group_text(self, group_id: str, text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 16000:
            raise OneBotError('onebot_text_invalid')
        return self.send_group_message(group_id, [{'type': 'text', 'data': {'text': text}}])

    def send_group_record(self, group_id: str, path: Path) -> str:
        path = Path(path).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise OneBotError('onebot_record_missing')
        return self.send_group_message(
            group_id, [{'type': 'record', 'data': {'file': path.as_uri()}}]
        )
