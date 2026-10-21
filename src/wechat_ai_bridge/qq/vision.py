"""Short-lived QQ image context for one member's recent group upload."""

import ipaddress
import socket
import time
import urllib.request
from collections import OrderedDict
from urllib.parse import urlparse

from wechat_ai_bridge.vision.inbox import (
    VisionPreparation, _CAPABILITY_ONLY, _DECLINED, _QUOTED, _REQUEST, _related_to_image,
)
from wechat_ai_bridge.vision.media import VisionError, VisionImage, normalize_image

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES = 128


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class QQVisionInbox:
    """Only the latest image per (group, sender) is retained in memory."""

    def __init__(self, groups, store, *, window_seconds: int = 120, clock=time.time,
                 fetch=None):
        if type(window_seconds) is not int or not 30 <= window_seconds <= 300:
            raise ValueError('qq_vision_window_invalid')
        self.groups = groups
        self.store = store
        self.window_seconds = window_seconds
        self.clock = clock
        self.fetch = fetch or self._fetch
        self._latest: OrderedDict[tuple[str, str], tuple[VisionImage, float]] = OrderedDict()

    def _fetch(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise VisionError('invalid_image')
        host = parsed.hostname.lower()
        if host in ('localhost',):
            raise VisionError('invalid_image')
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
        except OSError:
            raise VisionError('invalid_image') from None
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                raise VisionError('invalid_image') from None
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                raise VisionError('invalid_image')
        request = urllib.request.Request(url, headers={
            'User-Agent': 'wechat-ai-bridge-qq/0.1', 'Accept': 'image/*'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise VisionError('invalid_image')
                raw = response.read(_MAX_IMAGE_BYTES + 1)
        except VisionError:
            raise
        except Exception:
            raise VisionError('invalid_image') from None
        if len(raw) > _MAX_IMAGE_BYTES:
            raise VisionError('image_too_large')
        return raw

    def observe_event(self, event, self_id):
        if not isinstance(event, dict) or event.get('post_type') not in ('message', 'message_sent'):
            return False
        if event.get('message_type') != 'group':
            return False
        group_id = str(event.get('group_id', ''))
        sender_id = str(event.get('user_id', ''))
        own = str(self_id or '')
        if (not group_id or group_id not in self.groups or not self.store.is_allowed(group_id)
                or self.store.is_paused() or not sender_id or sender_id == own):
            return False
        message = event.get('message')
        if not isinstance(message, list):
            return False
        url = None
        for segment in message:
            if not isinstance(segment, dict) or segment.get('type') != 'image':
                continue
            data = segment.get('data')
            if not isinstance(data, dict):
                continue
            candidate = data.get('url') or data.get('file')
            if isinstance(candidate, str) and candidate.startswith(('http://', 'https://')):
                url = candidate
        if not url:
            return False
        try:
            image = normalize_image(self.fetch(url))
        except VisionError:
            return False
        now = self.clock()
        key = (group_id, sender_id)
        self._latest[key] = (image, now)
        self._latest.move_to_end(key)
        for old_key, (_, created) in list(self._latest.items()):
            if now - created > self.window_seconds:
                del self._latest[old_key]
        while len(self._latest) > _MAX_ENTRIES:
            self._latest.popitem(last=False)
        return True

    def prepare(self, message, *, guard, model=None, model_call=None) -> VisionPreparation | None:
        if not message.mentioned_bot or message.interaction != 'text':
            return None
        request = _QUOTED.sub('', message.text).strip()
        if _DECLINED.search(request) or _CAPABILITY_ONLY.search(request):
            return None
        key = (message.group_id, message.sender_id)
        entry = self._latest.get(key)
        recent = entry is not None and self.clock() - entry[1] <= self.window_seconds
        explicit = bool(_REQUEST.search(request))
        if not explicit and not recent:
            return None
        guard()
        if (message.group_id not in self.groups or not self.store.is_allowed(message.group_id)
                or self.store.is_paused()):
            self._latest.pop(key, None)
            return VisionPreparation(reply='当前群未启用看图，或机器人已暂停。')
        if not explicit and not _related_to_image(request, model, model_call or (lambda method, *args: method(*args))):
            return None
        if not recent:
            self._latest.pop(key, None)
            return VisionPreparation(reply=f'请先在这个群里发一张图片，再在{self.window_seconds}秒内 @ 我；我只看你自己最近发的一张。')
        guard()
        return VisionPreparation(image=entry[0])
