"""QQ gateway loop: webhook events in, shared delivery service out."""

import queue
import sys
import time

from wechat_ai_bridge.qq.config import QQSettings
from wechat_ai_bridge.qq.events import QQEventError, map_event
from wechat_ai_bridge.qq.webhook import QQWebhookServer


class QQGateway:
    def __init__(self, settings: QQSettings, client, service, *, self_id=None, vision=None):
        self.settings = settings
        self.client = client
        self.service = service
        self.vision = vision
        self.self_id = str(self_id) if self_id is not None else None
        self.events = queue.Queue(maxsize=1000)
        self.webhook = QQWebhookServer(settings, self._enqueue)
        self.received = 0
        self.natural_evaluated = 0
        self.dropped = 0
        self.errors = 0

    def _enqueue(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def tick(self) -> dict:
        received = natural = 0
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if self.self_id is None and event.get('self_id') is not None:
                self.self_id = str(event['self_id'])
            if self.vision is not None:
                self.vision.observe_event(event, self.self_id)
            try:
                message = map_event(event, self.self_id)
            except QQEventError:
                self.errors += 1
                continue
            if message is None:
                continue
            if not self.settings.conversations or message.group_id not in self.settings.conversations:
                continue
            try:
                result = self.service.handle(message)
            except Exception:
                self.errors += 1
                continue
            processed = getattr(result, 'processed', None)
            outcome = getattr(processed, 'outcome', None) or getattr(result, 'outcome', None)
            received += 1 if not message.is_from_bot else 0
            natural += 1 if str(outcome) == 'deferred' else 0
        try:
            pending = self.service.flush_pending()
        except Exception:
            self.errors += 1
            pending = []
        natural += len(pending)
        self.received += received
        self.natural_evaluated += natural
        return {'received': received, 'naturalEvaluated': natural}

    def run(self):
        info = self.client.login_info()
        user_id = info.get('user_id')
        if user_id is None:
            raise RuntimeError('qq_login_info_missing')
        self.self_id = str(user_id)
        self.webhook.start()
        try:
            while True:
                try:
                    self.tick()
                except Exception as error:
                    self.errors += 1
                    print('[qq] tick error: ' + type(error).__name__, file=sys.stderr, flush=True)
                time.sleep(0.5)
        finally:
            self.webhook.stop()

    def stop(self):
        self.webhook.stop()
