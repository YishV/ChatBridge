"""Local HTTP webhook receiver for NapCat OneBot 11 events."""

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from wechat_ai_bridge.qq.config import QQSettings

_MAX_BODY = 1024 * 1024


class QQWebhookServer:
    def __init__(self, settings: QQSettings, on_event):
        self.settings = settings
        self.on_event = on_event
        self.httpd = None
        self.thread = None
        self.port = settings.webhook_port
        self._server = self

    def _handler_class(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'wechat-ai-bridge-qq/0.1'

            def log_message(self, format, *args):
                return

            def _reply(self, status, payload=None):
                body = b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                if payload is not None:
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):
                if urlparse(self.path).path == '/health':
                    self._reply(200, {'ok': True})
                else:
                    self._reply(404)

            def do_POST(self):
                if urlparse(self.path).path != owner.settings.webhook_path:
                    self._reply(404)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    self._reply(400)
                    return
                if length < 2 or length > _MAX_BODY:
                    self._reply(413 if length > _MAX_BODY else 400)
                    return
                body = self.rfile.read(length)
                if owner.settings.webhook_secret and not self._authorized(body):
                    self._reply(401)
                    return
                if not owner.settings.webhook_secret and self.client_address[0] not in ('127.0.0.1', '::1'):
                    self._reply(403)
                    return
                try:
                    event = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    self._reply(400)
                    return
                if not isinstance(event, dict):
                    self._reply(400)
                    return
                try:
                    owner.on_event(event)
                except Exception:
                    self._reply(500)
                    return
                self._reply(204)

            def _authorized(self, body: bytes) -> bool:
                header = (self.headers.get('X-Signature') or self.headers.get('X-NapCat-Signature') or '').strip()
                if header.startswith('sha256='):
                    header = header[7:]
                expected = hmac.new(owner.settings.webhook_secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
                return bool(header) and hmac.compare_digest(header.lower(), expected.lower())

        return Handler

    def start(self):
        if self.httpd is not None:
            raise RuntimeError('webhook_already_started')
        self.httpd = ThreadingHTTPServer((self.settings.webhook_host, self.settings.webhook_port),
                                         self._handler_class())
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='qq-webhook', daemon=True)
        self.thread.start()
        return self.port

    def stop(self):
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None
