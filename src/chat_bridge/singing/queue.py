"""SQLite-backed queue for local RVC cover jobs."""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class QueueBusy(Exception):
    pass


@dataclass(frozen=True)
class SingingJob:
    id: int
    conversation_id: str
    is_group: bool
    sender_id: str
    query: str
    song_path: str
    status: str
    error_code: str | None = None
    output_path: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  is_group INTEGER NOT NULL,
  sender_id TEXT NOT NULL,
  query TEXT NOT NULL,
  song_path TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  output_path TEXT,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
"""


class SingingQueue:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript(_SCHEMA)
        self.connection.commit()
        self.connection.execute(
            "UPDATE jobs SET status='queued', started_at=NULL WHERE status='running'"
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def enqueue(self, conversation_id: str, is_group: bool, sender_id: str,
                query: str, song_path: str) -> int:
        with self.connection:
            active = self.connection.execute(
                "SELECT * FROM jobs WHERE conversation_id=? AND status IN ('queued','running') "
                "ORDER BY id DESC LIMIT 1", (conversation_id,)
            ).fetchone()
            if active:
                if active['song_path'] == song_path:
                    return int(active['id'])
                raise QueueBusy()
            count = self.connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued'"
            ).fetchone()[0]
            if count >= 50:
                raise QueueBusy()
            cursor = self.connection.execute(
                "INSERT INTO jobs(conversation_id,is_group,sender_id,query,song_path,status,created_at) "
                "VALUES(?,?,?,?,?, 'queued', ?)",
                (conversation_id, int(is_group), sender_id, query, song_path, time.time()),
            )
            return int(cursor.lastrowid)

    def claim_next(self) -> SingingJob | None:
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self.connection.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                (time.time(), row['id']),
            )
            row = self.connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row['id'],)
            ).fetchone()
        return self._job(row)

    def complete(self, job_id: int, output_path: str):
        with self.connection:
            self.connection.execute(
                "UPDATE jobs SET status='done', output_path=?, finished_at=? WHERE id=?",
                (output_path, time.time(), job_id),
            )

    def fail(self, job_id: int, error_code: str, *, retry: bool = False):
        with self.connection:
            if retry:
                self.connection.execute(
                    "UPDATE jobs SET status='queued', retry_count=retry_count+1, "
                    "error_code=?, started_at=NULL WHERE id=?",
                    (error_code, job_id),
                )
            else:
                self.connection.execute(
                    "UPDATE jobs SET status='failed', error_code=?, finished_at=? WHERE id=?",
                    (error_code, time.time(), job_id),
                )

    def recent_paths(self, conversation_id: str, limit: int = 5) -> list[str]:
        rows = self.connection.execute(
            "SELECT song_path FROM jobs WHERE conversation_id=? AND status='done' "
            "ORDER BY id DESC LIMIT ?", (conversation_id, limit)
        ).fetchall()
        return [row['song_path'] for row in rows]

    @staticmethod
    def _job(row) -> SingingJob:
        return SingingJob(
            int(row['id']), row['conversation_id'], bool(row['is_group']),
            row['sender_id'], row['query'], row['song_path'], row['status'],
            row['error_code'], row['output_path'],
        )
