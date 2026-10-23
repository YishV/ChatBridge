"""QQ gateway loop: webhook events in, shared delivery service out."""

import queue
import sys
import time
from pathlib import Path

from chat_bridge.qq.config import QQSettings
from chat_bridge.qq.events import QQEventError, map_event
from chat_bridge.qq.webhook import QQWebhookServer


class QQGateway:
    def __init__(self, settings: QQSettings, client, service, *, self_id=None, vision=None,
                 singing=None, meme_collector=None):
        self.settings = settings
        self.client = client
        self.service = service
        self.vision = vision
        self.singing = singing
        self.meme_collector = meme_collector
        self.self_id = str(self_id) if self_id is not None else None
        self.events = queue.Queue(maxsize=1000)
        self.webhook = QQWebhookServer(settings, self._enqueue)
        self.received = 0
        self.natural_evaluated = 0
        self.dropped = 0
        self.errors = 0

    def _collect_memes(self, event):
        collector = self.meme_collector
        if collector is None or not collector.enabled:
            return
        if event.get('post_type') not in ('message', 'message_sent'):
            return
        message_type = event.get('message_type')
        user_id = event.get('user_id')
        if message_type not in ('group', 'private') or user_id is None:
            return
        if str(user_id) == self.self_id:
            return
        if message_type == 'private':
            conversation_id = 'private:' + str(user_id)
            private = True
        else:
            group_id = event.get('group_id')
            if group_id is None:
                return
            conversation_id = str(group_id)
            private = False
        if conversation_id not in self.settings.conversations:
            return
        message = event.get('message')
        if not isinstance(message, list):
            return
        for segment in message:
            if not isinstance(segment, dict) or segment.get('type') != 'image':
                continue
            data = segment.get('data')
            if not isinstance(data, dict):
                continue
            for key in ('url', 'file'):
                value = data.get(key)
                if not isinstance(value, str) or not value:
                    continue
                if value.startswith(('http://', 'https://', 'file://')):
                    collector.submit_url(value, platform='qq', private=private)
                else:
                    try:
                        path = Path(value)
                        if path.is_file():
                            collector.submit_url(
                                path.resolve().as_uri(), platform='qq', private=private
                            )
                    except (OSError, ValueError):
                        pass

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
            self._collect_memes(event)
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
            if self.singing is not None:
                try:
                    if self.singing.handle(message):
                        received += 1 if not message.is_from_bot else 0
                        continue
                except Exception:
                    self.errors += 1
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
