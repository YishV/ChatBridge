"""Deterministic parsing of QQ singing requests."""

import re
from dataclasses import dataclass


_RANDOM_WORDS = frozenset({
    '', '歌', '首歌', '一首歌', '随便', '随机', '都行', '随便唱', '随机唱',
})
_COMMAND = re.compile(r'^/(翻唱|随机翻唱)(?:\s+(.*))?$')
_PREFIX = (
    r'(?:(?:你|能不能|可不可以|可以|能|帮我|给我|请|麻烦|'
    r'让(?:ai|AI|机器人|bot|你)?)\s*)?'
)
_NATURAL = re.compile(_PREFIX + r'(?:随便|随机)?(?:唱|来|翻唱)(?:一首|首|一下|一个)?\s*(.*)$')
_POINT = re.compile(r'^(?:点歌|我想听|放一首|放首)\s*(.*)$')
_EXPLICIT_GROUP = re.compile(
    _PREFIX + r'(?:点歌|(?:随便|随机)?(?:唱|来|翻唱)(?:一首|首|一下|一个))'
)
_TRAILING = ('吧', '呗', '呀', '啊', '吗', '呢', '了')
_LISTEN_SUFFIXES = ('来听听', '听听', '给我听', '听一下', '听一听')


def _clean_query(value: str) -> str:
    value = value.strip()
    changed = True
    while changed:
        changed = False
        for suffix in _TRAILING:
            if value.endswith(suffix):
                value = value[:-len(suffix)].strip()
                changed = True
        for suffix in _LISTEN_SUFFIXES:
            if value.endswith(suffix):
                value = value[:-len(suffix)].strip()
                changed = True
    return value


@dataclass(frozen=True)
class SingingRequest:
    query: str | None = None

    @property
    def random(self) -> bool:
        return self.query is None


def looks_like_singing(text: str) -> bool:
    return isinstance(text, str) and any(token in text for token in ('唱', '歌', '点歌'))


def parse_request(text: str, *, is_group: bool, mentioned: bool) -> SingingRequest | None:
    if not isinstance(text, str):
        return None
    text = re.sub(r'^\s*@\S+\s*', '', text).strip()
    if not text:
        return None
    command = _COMMAND.match(text)
    if command:
        action, query = command.groups()
        if action == '随机翻唱':
            return SingingRequest()
        query = _clean_query(query or '')
        return SingingRequest(None if query in _RANDOM_WORDS else query)
    point = _POINT.match(text)
    if point:
        query = _clean_query(point.group(1))
        return SingingRequest(None if query in _RANDOM_WORDS else query)
    if is_group and not mentioned and not _EXPLICIT_GROUP.match(text):
        return None
    natural = _NATURAL.match(text)
    if not natural:
        return None
    query = _clean_query(natural.group(1))
    if query in _RANDOM_WORDS:
        return SingingRequest()
    return SingingRequest(query) if query else SingingRequest()
