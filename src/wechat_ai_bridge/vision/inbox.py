"""Bounded, short-lived references; ordinary pictures never trigger uploads."""

import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from time import time

from wechat_ai_bridge.vision.media import VisionError, VisionImage
from wechat_ai_bridge.vision.resolver import ImageReference
from wechat_ai_bridge.wechat.native import _native_xml


IMAGE_FILE_TYPE = (8 << 32) | 49
MEDIA_TYPES = (3, 47, IMAGE_FILE_TYPE)
_VISUAL_VERB = r'(?:看|识别|分析|描述|读|解释)'
_PICTURE = r'(?:图片|照片|截图|表情包|图)'
_REQUEST = re.compile(rf'{_VISUAL_VERB}.{{0,20}}{_PICTURE}|{_PICTURE}.{{0,20}}(?:什么|啥|写|内容|怎么样)')
_DECLINED = re.compile(rf'(?:别|不要|不用|无需|不必|不准|禁止|不能|不需要|不想|没让|没有让|不让)[^，。！？,!?;；\n]{{0,12}}(?:{_VISUAL_VERB}|上传|发送|处理)[^，。！？,!?;；\n]{{0,20}}{_PICTURE}')
_CAPABILITY_ONLY = re.compile(rf'(?:你|机器人).{{0,3}}(?:能不能|会不会|可不可以|是否|能否|会|能|可以|支持|有)[^，。！？,!?;；\n]{{0,8}}{_VISUAL_VERB}{_PICTURE}')
_QUOTED = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"]*"|\x27[^\x27]*\x27|`[^`]*`')


@dataclass(frozen=True)
class VisionPreparation:
    image: VisionImage | None = None
    reply: str | None = None


class VisionInbox:
    def __init__(self, resolver, store, account_id: str, groups: frozenset[str], *,
                 window_seconds: int = 120, clock=time):
        if type(window_seconds) is not int or not 30 <= window_seconds <= 300:
            raise ValueError('vision_window_invalid')
        self.resolver, self.store, self.account_id, self.groups = resolver, store, account_id, groups
        self.window_seconds, self.clock = window_seconds, clock
        self._latest = OrderedDict()

    def clear_group(self, group_id):
        for key in list(self._latest):
            if key[0] == group_id:
                del self._latest[key]

    def observe(self, group_id, row) -> bool:
        key = (group_id, row.sender_id)
        if (group_id not in self.groups or not self.store.is_allowed(group_id) or self.store.is_paused()
                or not row.sender_id or row.sender_id == self.account_id or row.server_id <= 0
                or row.local_id <= 0 or not -30 <= self.clock() - row.create_time <= self.window_seconds):
            return False
        for old_key, ref in list(self._latest.items()):
            if self.clock() - ref.create_time > self.window_seconds:
                del self._latest[old_key]
        previous = self._latest.get(key)
        if previous is not None:
            if previous.local_type == 0 and row.create_time <= previous.create_time:
                return False
            if (row.create_time < previous.create_time or
                    row.shard == previous.shard and row.local_id <= previous.local_id):
                return False
            if row.create_time == previous.create_time and row.shard != previous.shard:
                # Different shards have no comparable local sequence. Refuse to
                # guess which image is latest when second-resolution times tie.
                self._latest[key] = replace(previous, local_type=0)
                return False
        valid = row.local_type == 3
        if row.local_type == IMAGE_FILE_TYPE:
            root = _native_xml(row.text)
            valid = (root is not None and root.tag == 'msg' and root.findtext('appmsg/type') == '8'
                     and (root.findtext('appmsg/appattach/fileext') or '').lower() in ('gif', 'png', 'jpg', 'jpeg', 'webp'))
        if not valid:
            self._latest.pop(key, None)
            return False
        ref = ImageReference(group_id, row.sender_id, row.shard, row.local_id, row.server_id,
                             row.create_time, local_type=row.local_type)
        self._latest[key] = ref
        self._latest.move_to_end(key)
        while len(self._latest) > 128:
            self._latest.popitem(last=False)
        return True

    def prepare(self, message, *, guard) -> VisionPreparation | None:
        request = _QUOTED.sub('', message.text)
        if (not message.mentioned_bot or message.interaction != 'text'
                or not _REQUEST.search(request) or _DECLINED.search(request)
                or _CAPABILITY_ONLY.search(request)):
            return None
        guard()
        key = (message.group_id, message.sender_id)
        if (message.group_id not in self.groups or not self.store.is_allowed(message.group_id)
                or self.store.is_paused()):
            self._latest.pop(key, None)
            return VisionPreparation(reply='当前群未启用看图，或机器人已暂停。')
        ref = self._latest.get(key)
        if ref is not None and ref.local_type == 0 and self.clock() - ref.create_time <= self.window_seconds:
            return VisionPreparation(reply='这几张图的先后顺序暂时无法确定。请隔一秒重新发一张图片，再 @ 我看图。')
        if ref is None or not -30 <= self.clock() - ref.create_time <= self.window_seconds:
            self._latest.pop(key, None)
            return VisionPreparation(reply=f'请先在这个群里发一张图片，再在{self.window_seconds}秒内 @ 我要求看图；我只看你自己最近发的一张。')
        try:
            image = self.resolver.resolve(ref)
        except VisionError:
            return VisionPreparation(reply='这张图暂时没能读取。请先在电脑微信打开原图，等下载完成后重新发图并 @ 我；我不会拿别人的图来猜。')
        guard()
        return VisionPreparation(image=image)
