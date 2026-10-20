"""Persist administrator controls and message IDs without chat transcripts."""

import sqlite3
import re
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

    def claim_event(self, message_id: str, group_id: str) -> bool:
        """Claim every admitted event, including silence and failures, without its text."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO seen_events VALUES (?, ?, ?)",
                (message_id, group_id, datetime.now(timezone.utc).isoformat()),
            )
            return cursor.rowcount == 1

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

    def finish_delivery(self, message_id: str, *, confirmed: bool, server_message_id: str | None = None) -> None:
        status = "confirmed" if confirmed and server_message_id else "unconfirmed"
        with self.connection:
            self.connection.execute(
                "UPDATE deliveries SET status = ?, server_message_id = ?, updated_at = ? "
                "WHERE message_id = ? AND status = 'pending'",
                (status, server_message_id if status == "confirmed" else None,
                 datetime.now(timezone.utc).isoformat(), message_id),
            )

    def delivery_status(self, message_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM deliveries WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row[0] if row else None
