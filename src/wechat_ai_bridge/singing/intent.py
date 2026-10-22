"""Deterministic parsing of QQ singing requests."""

import re
from dataclasses import dataclass


_RANDOM_WORDS = frozenset({
    '', '歌', '首歌', '一首歌', '随便', '随机', '都行', '随便唱', '随机唱',
})
_COMMAND = re.compile(r'^/(翻唱|随机翻唱)(?:\s+(.*))?$')
_NATURAL = re.compile(r'^(?:随便|随机)?(?:唱|来|翻唱)(?:一首|首|一下|一个)?\s*(.*)$')


@dataclass(frozen=True)
class SingingRequest:
    query: str | None = None

    @property
    def random(self) -> bool:
        return self.query is None


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
        query = (query or '').strip()
        return SingingRequest(None if query in _RANDOM_WORDS else query)
    if is_group and not mentioned:
        return None
    natural = _NATURAL.match(text)
    if not natural:
        return None
    query = natural.group(1).strip().rstrip('吧呗呀啊')
    if query in _RANDOM_WORDS:
        return SingingRequest()
    return SingingRequest(query) if query else SingingRequest()
