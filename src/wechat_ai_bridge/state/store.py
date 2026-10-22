"""Persist administrator controls, message IDs, and authorized chat history."""

import sqlite3
import re
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS messages (message_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS seen_events (message_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.connection.execute("INSERT OR IGNORE INTO seen_events SELECT message_id, group_id, created_at FROM messages")
        self.connection.execute("CREATE TABLE IF NOT EXISTS deliveries (message_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, status TEXT NOT NULL, server_message_id TEXT, updated_at TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS audit (at TEXT NOT NULL, action TEXT NOT NULL)")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS chat_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, group_id TEXT NOT NULL, "
            "role TEXT NOT NULL CHECK(role IN ('user', 'assistant')), member_id TEXT NOT NULL, "
            "sender_name TEXT, content TEXT NOT NULL, interaction TEXT NOT NULL DEFAULT 'text', "
            "created_at TEXT NOT NULL, UNIQUE(group_id, message_id, role))"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS chat_history_group_id_id ON chat_history (group_id, id DESC)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS media_deliveries ("
            "message_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, asset_id TEXT NOT NULL, "
            "status TEXT NOT NULL, server_message_id TEXT, created_at TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS media_group_created ON media_deliveries (group_id, created_at DESC)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def record_model_failure(self, code: str, attempt: int) -> None:
        if (not isinstance(code, str) or not re.fullmatch(r'model_[a-z0-9_]{1,64}', code)
                or type(attempt) is not int or not 1 <= attempt <= 2):
            raise ValueError('invalid_model_diagnostic')
        with self.connection:
            self._audit(f'model_failure:{code}:attempt{attempt}')

    def allow_group(self, group_id: str) -> None:
        if not group_id.strip():
            raise ValueError("群 ID 不能为空")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO groups VALUES (?)", (group_id,))
            self._audit("allow_group")

    def deny_group(self, group_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM groups WHERE group_id = ?", (group_id,))
            self._audit("deny_group")

    def allowed_groups(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SELECT group_id FROM groups ORDER BY group_id")]

    def is_allowed(self, group_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone() is not None

    def set_paused(self, paused: bool) -> None:
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO controls VALUES ('paused', ?)", ("1" if paused else "0",))
            self._audit("pause" if paused else "resume")

    def is_paused(self) -> bool:
        row = self.connection.execute("SELECT value FROM controls WHERE key = 'paused'").fetchone()
        return row is not None and row[0] == "1"

    def _audit(self, action: str) -> None:
        self.connection.execute("INSERT INTO audit VALUES (?, ?)", (datetime.now(timezone.utc).isoformat(), action))

    @staticmethod
    def _validate_chat(chat: dict) -> tuple[str, str | None, str, str]:
        if not isinstance(chat, dict):
            raise ValueError("invalid_chat_history")
        member_id = chat.get("member_id")
        sender_name = chat.get("sender_name")
        content = chat.get("content")
        interaction = chat.get("interaction")
        if (not isinstance(member_id, str) or not re.fullmatch(r"member_[0-9a-f]{12}", member_id)
                or (sender_name is not None and (not isinstance(sender_name, str) or len(sender_name) > 64))
                or not isinstance(content, str) or not content
                or interaction not in ("text", "pat")):
            raise ValueError("invalid_chat_history")
        try:
            encoded = content.encode("utf-8")
            if sender_name is not None:
                sender_name.encode("utf-8")
        except UnicodeError:
            raise ValueError("invalid_chat_history") from None
        if len(encoded) > 64 * 1024:
            raise ValueError("invalid_chat_history")
        return member_id, sender_name, content, interaction

    def _insert_chat(self, message_id: str, group_id: str, role: str,
                     chat: tuple[str, str | None, str, str]) -> None:
        self.connection.execute(
            "INSERT INTO chat_history "
            "(message_id, group_id, role, member_id, sender_name, content, interaction, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, group_id, role, *chat, datetime.now(timezone.utc).isoformat()),
        )

    def claim_event(self, message_id: str, group_id: str, *, chat: dict | None = None) -> bool:
        """Claim an admitted event and atomically persist its supplied user text."""
        validated = self._validate_chat(chat) if chat is not None else None
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO seen_events VALUES (?, ?, ?)",
                (message_id, group_id, datetime.now(timezone.utc).isoformat()),
            )
            claimed = cursor.rowcount == 1
            if claimed and validated is not None:
                self._insert_chat(message_id, group_id, "user", validated)
            return claimed

    def clear_chat_history(self, group_id: str) -> int:
        """Delete one conversation's persisted transcript without touching other users."""
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError('invalid_group_id')
        with self.connection:
            cursor = self.connection.execute(
                'DELETE FROM chat_history WHERE group_id = ?', (group_id,)
            )
            return int(cursor.rowcount)

    def load_chat_history(self, group_id: str, limit: int = 50) -> list[dict]:
        """Read recent authorized context in original order without expiring the archive."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_history_limit")
        if not self.is_allowed(group_id):
            return []
        cursor = self.connection.execute(
            "SELECT id, message_id, group_id, role, member_id, sender_name, content, interaction, created_at "
            "FROM (SELECT * FROM chat_history WHERE group_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (group_id, limit),
        )
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor]

    def has_chat_history(self) -> bool:
        """Check for saved identities without reading any transcript content."""
        return self.connection.execute("SELECT 1 FROM chat_history LIMIT 1").fetchone() is not None

    def reserve(self, message_id: str, group_id: str, cooldown_seconds: int, hourly_quota: int) -> str:
        """Atomically reserve an ID after group limits; never store message content."""
        now = datetime.now(timezone.utc)
        hour_ago = (now - timedelta(hours=1)).isoformat()
        cooldown_since = (now - timedelta(seconds=cooldown_seconds)).isoformat()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.connection.execute("SELECT 1 FROM messages WHERE message_id = ?", (message_id,)).fetchone():
                return "duplicate"
            if self.connection.execute(
                "SELECT 1 FROM messages WHERE group_id = ? AND created_at > ? LIMIT 1", (group_id, cooldown_since)
            ).fetchone():
                return "rate_limited"
            count = self.connection.execute(
                "SELECT COUNT(*) FROM messages WHERE group_id = ? AND created_at > ?", (group_id, hour_ago)
            ).fetchone()[0]
            if count >= hourly_quota:
                return "rate_limited"
            self.connection.execute("INSERT INTO messages VALUES (?, ?, ?)", (message_id, group_id, now.isoformat()))
            self.connection.execute("DELETE FROM messages WHERE created_at < ?", ((now - timedelta(days=2)).isoformat(),))
            return "reserved"

    def begin_delivery(self, message_id: str, group_id: str) -> bool:
        """Create a one-shot send record; a later event must not trigger another send."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?, 'pending', NULL, ?)",
                (message_id, group_id, datetime.now(timezone.utc).isoformat()),
            )
            return cursor.rowcount == 1

    def finish_delivery(self, message_id: str, *, confirmed: bool, server_message_id: str | None = None,
                        chat: dict | None = None) -> None:
        """Finish a one-shot delivery and persist only a newly confirmed assistant turn."""
        status = "confirmed" if confirmed and server_message_id else "unconfirmed"
        validated = self._validate_chat(chat) if chat is not None else None
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE deliveries SET status = ?, server_message_id = ?, updated_at = ? "
                "WHERE message_id = ? AND status = 'pending'",
                (status, server_message_id if status == "confirmed" else None,
                 datetime.now(timezone.utc).isoformat(), message_id),
            )
            if cursor.rowcount == 1 and status == "confirmed" and validated is not None:
                group_id = self.connection.execute(
                    "SELECT group_id FROM deliveries WHERE message_id = ?", (message_id,)
                ).fetchone()[0]
                self._insert_chat(message_id, group_id, "assistant", validated)

    def delivery_status(self, message_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM deliveries WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row[0] if row else None

    def recent_media(self, group_id: str, limit: int = 5) -> list[str]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('invalid_media_recent_limit')
        if not self.is_allowed(group_id):
            return []
        return [row[0] for row in self.connection.execute(
            'SELECT asset_id FROM media_deliveries WHERE group_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?',
            (group_id, limit))]

    def media_available(self, group_id: str, *, cooldown_seconds: int = 60) -> bool:
        if type(cooldown_seconds) is not int or not 0 <= cooldown_seconds <= 3600:
            raise ValueError('invalid_media_cooldown')
        if self.is_paused() or not self.is_allowed(group_id):
            return False
        since = (datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)).isoformat()
        return self.connection.execute(
            'SELECT 1 FROM media_deliveries WHERE group_id=? AND created_at>? LIMIT 1',
            (group_id, since)).fetchone() is None

    def begin_media_delivery(self, message_id: str, group_id: str, asset_id: str, *,
                             cooldown_seconds: int = 60, recent_limit: int = 5) -> bool:
        if not isinstance(asset_id, str) or not re.fullmatch(r'[0-9]{3,6}', asset_id):
            raise ValueError('invalid_media_asset')
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            if not self.media_available(group_id, cooldown_seconds=cooldown_seconds):
                return False
            if asset_id in self.recent_media(group_id, recent_limit):
                return False
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO media_deliveries VALUES (?, ?, ?, 'pending', NULL, ?)",
                (message_id, group_id, asset_id, datetime.now(timezone.utc).isoformat()))
            return cursor.rowcount == 1

    def finish_media_delivery(self, message_id: str, *, confirmed: bool,
                              server_message_id: str | None = None, chat: dict | None = None) -> None:
        status = 'confirmed' if confirmed and server_message_id else 'unconfirmed'
        validated = self._validate_chat(chat) if chat is not None else None
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE media_deliveries SET status=?, server_message_id=? WHERE message_id=? AND status='pending'",
                (status, server_message_id if status == 'confirmed' else None, message_id))
            if cursor.rowcount == 1 and status == 'confirmed' and validated is not None:
                group = self.connection.execute(
                    'SELECT group_id FROM media_deliveries WHERE message_id=?', (message_id,)).fetchone()[0]
                history_id = 'media:' + hashlib.sha256(message_id.encode()).hexdigest()
                self._insert_chat(history_id, group, 'assistant', validated)
