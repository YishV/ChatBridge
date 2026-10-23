"""Private-chat memory facade used by ChatService."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.memory.store import MemoryStore
from wechat_ai_bridge.memory.worker import MemoryTask, MemoryWorker


class ChatMemory:
    def __init__(self, store: MemoryStore, platform: str, model, *,
                 worker: MemoryWorker | None = None,
                 update_every: int = 3, summary_every: int = 30,
                 queue_size: int = 64,
                 clear_history: Callable[[IncomingMessage], None] | None = None):
        self.store = store
        self.platform = platform
        self.worker = worker or MemoryWorker(
            model, store, platform,
            update_every=update_every, summary_every=summary_every,
            queue_size=queue_size,
        )
        self.clear_history = clear_history
        self._command_ids: set[str] = set()
        self._turns: dict[str, int] = defaultdict(int)

    def format(self, message: IncomingMessage) -> str:
        if message.is_group:
            return ''
        return self.store.format_for_prompt(
            self.platform, message.group_id, message.text
        )

    def handle_command(self, message: IncomingMessage) -> str | None:
        if message.is_group:
            return None
        text = message.text.strip()
        if any(text.startswith(prefix) for prefix in (
                '清空我的记忆', '清空记忆', '查看记忆', '我的记忆',
                '记住', '记一下', '记下来', '保存记忆', '帮我记住', '帮我记一下',
                '忘记', '忘掉')):
            self._command_ids.add(message.message_id)
        for prefix in ('清空我的记忆', '清空记忆'):
            if text.startswith(prefix):
                count = self.store.clear(self.platform, message.group_id)
                if self.clear_history is not None:
                    self.clear_history(message)
                return f'好，已经清掉 {count} 条关于你的记忆和这段聊天记录。' if count else '好，这段聊天记录已经清掉了。'
        for prefix in ('查看记忆', '我的记忆'):
            if text.startswith(prefix):
                items = self.store.list(self.platform, message.group_id, limit=20)
                summary = self.store.get_summary(self.platform, message.group_id)
                if not items and not summary:
                    return '现在还没有关于你的长期记忆。'
                lines = []
                if summary:
                    lines.append(f'摘要：{summary}')
                lines.extend(f'- {item.content}' for item in items)
                return ('我记得这些：\n' + '\n'.join(lines))[:800]
        for prefix in ('记住', '记一下', '记下来', '保存记忆', '帮我记住', '帮我记一下'):
            if text.startswith(prefix):
                content = text[len(prefix):].lstrip('：:，, ').strip()
                if not content:
                    return '要记住什么？'
                try:
                    self.store.add(
                        self.platform, message.group_id, 'note', content,
                        confidence=1.0, importance=0.8,
                        source_message_id=message.message_id,
                    )
                except ValueError:
                    return '这条没记住，换个短一点的说法吧。'
                return f'好，记住了：{content[:200]}'
        for prefix in ('忘记', '忘掉'):
            if text.startswith(prefix):
                query = text[len(prefix):].lstrip('：:，, ').strip()
                if not query:
                    return '要忘记什么？'
                count = self.store.delete(self.platform, message.group_id, query)
                return f'已经忘掉 {count} 条相关记忆。' if count else f'没找到关于「{query}」的记忆。'
        return None

    def submit(self, message: IncomingMessage, reply: str,
               recent: tuple[tuple[str, str], ...]) -> None:
        if message.is_group or message.message_id in self._command_ids:
            return
        key = message.group_id
        self._turns[key] += 1
        self.worker.submit(MemoryTask(
            message=message, reply=reply, recent=recent,
            turn_number=self._turns[key],
        ))
