"""Local HTTP webhook receiver for NapCat OneBot 11 events."""

import hashlib
import hmac
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from chat_bridge.qq.config import QQSettings

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
            server_version = 'chatbridge-qq/0.1'

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
                body = self._read_body()
                if body is None:
                    self._reply(400)
                    return
                if owner.settings.webhook_secret and not self._authorized(body):
                    self._reply(401)
                    return
                if not owner.settings.webhook_secret and self.client_address[0] not in ('127.0.0.1', '::1'):
                    self._reply(403)
                    return
                try:
                    event = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    print('[qq-webhook] invalid json content-type=%r length=%d transfer=%r' % (
                        self.headers.get('Content-Type'), len(body),
                        self.headers.get('Transfer-Encoding')), file=sys.stderr, flush=True)
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

            def _read_body(self):
                transfer = (self.headers.get('Transfer-Encoding') or '').lower()
                if 'chunked' in transfer:
                    chunks = []
                    total = 0
                    try:
                        while True:
                            line = self.rfile.readline(1024).strip()
                            if not line:
                                break
                            size = int(line.split(b';', 1)[0], 16)
                            if size == 0:
                                while True:
                                    trailer = self.rfile.readline(1024)
                                    if trailer in (b'\r\n', b'\n', b''):
                                        break
                                break
                            total += size
                            if total > _MAX_BODY:
                                return None
                            chunks.append(self.rfile.read(size))
                            self.rfile.read(2)
                    except (ValueError, OSError):
                        return None
                    body = b''.join(chunks)
                else:
                    try:
                        length = int(self.headers.get('Content-Length', '0'))
                    except ValueError:
                        return None
                    if length < 2 or length > _MAX_BODY:
                        return None
                    body = self.rfile.read(length)
                return body if len(body) >= 2 else None

            def _authorized(self, body: bytes) -> bool:
                header = (self.headers.get('X-Signature') or self.headers.get('X-NapCat-Signature') or '').strip()
                algorithms = (hashlib.sha1,)
                if header.startswith('sha256='):
                    header = header[7:]
                    algorithms = (hashlib.sha256,)
                elif header.startswith('sha1='):
                    header = header[5:]
                if not header:
                    return False
                for algorithm in algorithms:
                    expected = hmac.new(owner.settings.webhook_secret.encode('utf-8'), body, algorithm).hexdigest()
                    if hmac.compare_digest(header.lower(), expected.lower()):
                        return True
                return False

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
