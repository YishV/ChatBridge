"""Per-user private-chat memory with simple local retrieval."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_MEMORY_KINDS = frozenset({
    'preference', 'identity', 'event', 'promise', 'relationship', 'note',
})
_MAX_CONTENT = 500
_MAX_KEY = 100
_MAX_SUMMARY = 1200


@dataclass(frozen=True)
class MemoryItem:
    id: int
    kind: str
    key: str | None
    content: str
    confidence: float
    importance: float
    updated_at: str
    platform: str = ''
    user_key: str = ''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _normalize(value: str) -> str:
    return re.sub(r'[\s\W_]+', '', value.casefold())


class MemoryStore:
    """SQLite-backed memories and rolling summaries.

    One row set per ``platform/user_key``; private conversations only.
    Connections are short-lived so background extraction can write safely.
    """

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        user_key TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        memory_key TEXT,
                        content TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        importance REAL NOT NULL,
                        source_message_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS memories_user
                        ON memories(platform, user_key, importance DESC, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS conversation_summaries (
                        platform TEXT NOT NULL,
                        user_key TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(platform, user_key)
                    );
                    """
                )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=5000')
        return connection

    @staticmethod
    def _clean_key(platform: str, user_key: str) -> tuple[str, str]:
        if not isinstance(platform, str) or platform not in ('qq', 'wechat'):
            raise ValueError('memory_platform_invalid')
        if not isinstance(user_key, str) or not user_key.strip():
            raise ValueError('memory_user_key_invalid')
        return platform, user_key.strip()

    def add(self, platform: str, user_key: str, kind: str, content: str, *,
            key: str | None = None, confidence: float = 0.8,
            importance: float = 0.5, source_message_id: str | None = None) -> int:
        platform, user_key = self._clean_key(platform, user_key)
        if kind not in _MEMORY_KINDS:
            raise ValueError('memory_kind_invalid')
        content = ' '.join(str(content).split()).strip()
        if not content or len(content) > _MAX_CONTENT:
            raise ValueError('memory_content_invalid')
        if key is not None:
            key = str(key).strip()
            if not key or len(key) > _MAX_KEY:
                raise ValueError('memory_key_invalid')
        for value, name in ((confidence, 'confidence'), (importance, 'importance')):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f'memory_{name}_invalid')
        now = _now()
        connection = self._connect()
        try:
            with connection:
                row = None
                if key:
                    row = connection.execute(
                        'SELECT id FROM memories WHERE platform=? AND user_key=? AND memory_key=?',
                        (platform, user_key, key),
                    ).fetchone()
                else:
                    row = connection.execute(
                        'SELECT id FROM memories WHERE platform=? AND user_key=? AND content=?',
                        (platform, user_key, content),
                    ).fetchone()
                if row is not None:
                    connection.execute(
                        'UPDATE memories SET kind=?, memory_key=?, content=?, confidence=?, '
                        'importance=?, source_message_id=?, updated_at=? WHERE id=?',
                        (kind, key, content, float(confidence), float(importance),
                         source_message_id, now, row['id']),
                    )
                    return int(row['id'])
                cursor = connection.execute(
                    'INSERT INTO memories(platform, user_key, kind, memory_key, content, '
                    'confidence, importance, source_message_id, created_at, updated_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (platform, user_key, kind, key, content, float(confidence),
                     float(importance), source_message_id, now, now),
                )
                return int(cursor.lastrowid)
        finally:
            connection.close()

    def delete(self, platform: str, user_key: str, query: str | None = None) -> int:
        platform, user_key = self._clean_key(platform, user_key)
        connection = self._connect()
        try:
            with connection:
                if query is None or not str(query).strip():
                    cursor = connection.execute(
                        'DELETE FROM memories WHERE platform=? AND user_key=?',
                        (platform, user_key),
                    )
                else:
                    term = str(query).strip()
                    cursor = connection.execute(
                        'DELETE FROM memories WHERE platform=? AND user_key=? '
                        'AND (memory_key LIKE ? OR content LIKE ?)',
                        (platform, user_key, f'%{term}%', f'%{term}%'),
                    )
                return int(cursor.rowcount)
        finally:
            connection.close()

    def clear(self, platform: str, user_key: str) -> int:
        platform, user_key = self._clean_key(platform, user_key)
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    'DELETE FROM memories WHERE platform=? AND user_key=?',
                    (platform, user_key),
                )
                connection.execute(
                    'DELETE FROM conversation_summaries WHERE platform=? AND user_key=?',
                    (platform, user_key),
                )
                return int(cursor.rowcount)
        finally:
            connection.close()

    def list(self, platform: str, user_key: str, limit: int = 20) -> list[MemoryItem]:
        platform, user_key = self._clean_key(platform, user_key)
        limit = max(1, min(int(limit), 100))
        connection = self._connect()
        try:
            rows = connection.execute(
                'SELECT id, kind, memory_key, content, confidence, importance, updated_at '
                'FROM memories WHERE platform=? AND user_key=? '
                'ORDER BY importance DESC, updated_at DESC LIMIT ?',
                (platform, user_key, limit),
            ).fetchall()
            return [
                MemoryItem(
                    id=int(row['id']), kind=row['kind'], key=row['memory_key'],
                    content=row['content'], confidence=float(row['confidence']),
                    importance=float(row['importance']), updated_at=row['updated_at'],
                    platform=platform, user_key=user_key,
                )
                for row in rows
            ]
        finally:
            connection.close()

    def list_all(self, query: str = '', limit: int = 200) -> list[MemoryItem]:
        limit = max(1, min(int(limit), 500))
        term = str(query).strip()
        connection = self._connect()
        try:
            if term:
                rows = connection.execute(
                    'SELECT id, platform, user_key, kind, memory_key, content, confidence, '
                    'importance, updated_at FROM memories '
                    'WHERE memory_key LIKE ? OR content LIKE ? '
                    'ORDER BY updated_at DESC, id DESC LIMIT ?',
                    (f'%{term}%', f'%{term}%', limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    'SELECT id, platform, user_key, kind, memory_key, content, confidence, '
                    'importance, updated_at FROM memories '
                    'ORDER BY updated_at DESC, id DESC LIMIT ?',
                    (limit,),
                ).fetchall()
            return [
                MemoryItem(
                    id=int(row['id']), kind=row['kind'], key=row['memory_key'],
                    content=row['content'], confidence=float(row['confidence']),
                    importance=float(row['importance']), updated_at=row['updated_at'],
                    platform=row['platform'], user_key=row['user_key'],
                )
                for row in rows
            ]
        finally:
            connection.close()

    def delete_by_id(self, memory_id: int) -> bool:
        if type(memory_id) is not int or memory_id < 1:
            raise ValueError('memory_id_invalid')
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    'DELETE FROM memories WHERE id=?', (memory_id,)
                )
                return cursor.rowcount == 1
        finally:
            connection.close()

    def save_summary(self, platform: str, user_key: str, summary: str,
                     message_count: int = 0) -> None:
        platform, user_key = self._clean_key(platform, user_key)
        summary = ' '.join(str(summary).split()).strip()
        if not summary or len(summary) > _MAX_SUMMARY:
            raise ValueError('memory_summary_invalid')
        if type(message_count) is not int or message_count < 0:
            raise ValueError('memory_summary_count_invalid')
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    'INSERT INTO conversation_summaries(platform, user_key, summary, message_count, updated_at) '
                    'VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT(platform, user_key) DO UPDATE SET '
                    'summary=excluded.summary, message_count=excluded.message_count, updated_at=excluded.updated_at',
                    (platform, user_key, summary, message_count, _now()),
                )
        finally:
            connection.close()

    def get_summary(self, platform: str, user_key: str) -> str | None:
        platform, user_key = self._clean_key(platform, user_key)
        connection = self._connect()
        try:
            row = connection.execute(
                'SELECT summary FROM conversation_summaries WHERE platform=? AND user_key=?',
                (platform, user_key),
            ).fetchone()
            return row['summary'] if row else None
        finally:
            connection.close()

    def format_for_prompt(self, platform: str, user_key: str, query: str = '',
                          limit: int = 6) -> str:
        platform, user_key = self._clean_key(platform, user_key)
        items = self.list(platform, user_key, limit=30)
        summary = self.get_summary(platform, user_key)
        normalized_query = _normalize(query)
        if normalized_query:
            def score(item: MemoryItem):
                text = _normalize(item.content)
                match = 1 if normalized_query in text or text in normalized_query else 0
                return (match, item.importance, item.updated_at)
        else:
            def score(item: MemoryItem):
                return (item.importance, item.updated_at)
        items = sorted(items, key=score, reverse=True)[:max(1, min(int(limit), 10))]
        lines = []
        if summary:
            lines.append(f'较早对话摘要：{summary}')
        lines.extend(f'- {item.content}' for item in items)
        if not lines:
            return ''
        return (
            '[这个用户的长期记忆，仅当前私聊可用。只在与当前话题相关时自然使用，'
            '不要逐条复述，也不要把这些内容带到群聊]\n' + '\n'.join(lines)
        )
