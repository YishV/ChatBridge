"""Shared local meme library imported from QQ/WeChat chats."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from wechat_ai_bridge.media.memes import MemeAsset, MemeCatalog


PUBLIC_ID_BASE = 900000


@dataclass(frozen=True)
class LocalMeme:
    id: str
    description: str
    tags: tuple[str, ...]
    path: Path
    platform: str
    score: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.parent / ('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalMemeLibrary:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.blobs = self.root / 'blobs'
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'index.sqlite3'
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS local_memes ('
                    'id INTEGER PRIMARY KEY AUTOINCREMENT, '
                    'hash TEXT NOT NULL UNIQUE, '
                    'path TEXT NOT NULL, '
                    'description TEXT NOT NULL, '
                    'tags TEXT NOT NULL, '
                    'platform TEXT NOT NULL, '
                    'score REAL NOT NULL, '
                    'enabled INTEGER NOT NULL DEFAULT 1, '
                    'created_at TEXT NOT NULL)'
                )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=5000')
        return connection

    @staticmethod
    def public_id(row_id: int) -> str:
        return str(PUBLIC_ID_BASE + int(row_id))

    @staticmethod
    def _local_id(public_id: str) -> int | None:
        if not isinstance(public_id, str) or not public_id.isdigit():
            return None
        value = int(public_id) - PUBLIC_ID_BASE
        return value if value > 0 else None

    def has_hash(self, digest: str) -> bool:
        connection = self._connect()
        try:
            return connection.execute(
                'SELECT 1 FROM local_memes WHERE hash=?', (digest,)
            ).fetchone() is not None
        finally:
            connection.close()

    def count_today(self, platform: str | None = None) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        connection = self._connect()
        try:
            if platform in ('qq', 'wechat'):
                row = connection.execute(
                    'SELECT COUNT(*) FROM local_memes WHERE substr(created_at,1,10)=? AND platform=?',
                    (day, platform),
                ).fetchone()
            else:
                row = connection.execute(
                    'SELECT COUNT(*) FROM local_memes WHERE substr(created_at,1,10)=?',
                    (day,),
                ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def add(self, *, digest: str, image_data: bytes, description: str,
            tags: tuple[str, ...], platform: str, score: float) -> LocalMeme | None:
        if platform not in ('qq', 'wechat') or not isinstance(image_data, bytes) or not image_data:
            return None
        if self.has_hash(digest):
            return None
        description = ' '.join(str(description).split())[:300]
        tags = tuple(str(tag).strip()[:30] for tag in tags if str(tag).strip())[:12]
        if not description or not tags:
            return None
        path = self.blobs / (digest + '.jpg')
        try:
            self.blobs.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                _atomic_write(path, image_data)
            connection = self._connect()
            try:
                with connection:
                    cursor = connection.execute(
                        'INSERT INTO local_memes(hash, path, description, tags, platform, score, created_at) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (digest, str(path.resolve()), description, json.dumps(tags, ensure_ascii=False),
                         platform, float(score), _now()),
                    )
                    row_id = int(cursor.lastrowid)
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return None
        return LocalMeme(
            self.public_id(row_id), description, tags, path.resolve(), platform, float(score)
        )

    def _rows(self) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                'SELECT id, description, tags, path, platform, score '
                'FROM local_memes WHERE enabled=1 ORDER BY id DESC'
            ).fetchall()
        finally:
            connection.close()

    def assets(self) -> list[LocalMeme]:
        items = []
        for row in self._rows():
            path = Path(row['path'])
            if not path.is_file():
                continue
            try:
                tags = tuple(json.loads(row['tags']))
            except (ValueError, TypeError):
                continue
            items.append(LocalMeme(
                self.public_id(row['id']), row['description'], tags,
                path, row['platform'], float(row['score']),
            ))
        return items

    def get(self, public_id: str) -> LocalMeme | None:
        local_id = self._local_id(public_id)
        if local_id is None:
            return None
        return next((item for item in self.assets() if item.id == public_id), None)

    def prepare(self, public_id: str) -> Path:
        item = self.get(public_id)
        if item is None or not item.path.is_file():
            raise ValueError('local_meme_missing')
        return item.path

    def candidates(self, text: str, *, excluded=(), limit: int = 12) -> list[LocalMeme]:
        limit = max(1, min(int(limit), 30))
        excluded = set(excluded)
        items = [item for item in self.assets() if item.id not in excluded]
        words = set(re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]', str(text).lower()[:4000]))
        pairs = {str(text)[i:i + 2].lower() for i in range(min(len(str(text)) - 1, 4000))}

        def score(item: LocalMeme) -> int:
            haystack = (item.description + ' ' + ' '.join(item.tags)).lower()
            return sum(3 for pair in pairs if pair in haystack) + sum(1 for word in words if word in haystack)

        ranked = sorted(items, key=lambda item: (-score(item), -item.score, item.id))
        if ranked and score(ranked[0]) > 0:
            return ranked[:limit]
        return ranked[:limit]


class CompositeMemeCatalog:
    """Prefer local imported memes, then fall back to the official catalog."""

    def __init__(self, official: MemeCatalog, local: LocalMemeLibrary,
                 local_first: bool = True):
        self.official = official
        self.local = local
        self.local_first = local_first

    @property
    def assets(self):
        local = tuple(self.local.assets())
        official = tuple(self.official.assets)
        return local + official if self.local_first else official + local

    def candidates(self, text: str, *, excluded=(), limit: int = 12):
        excluded = set(excluded)
        local = self.local.candidates(text, excluded=excluded, limit=limit)
        official = self.official.candidates(text, excluded=excluded, limit=limit)
        ordered = local + official if self.local_first else official + local
        result = []
        seen = set()
        for item in ordered:
            if item.id not in seen:
                result.append(item)
                seen.add(item.id)
            if len(result) >= limit:
                break
        return result

    def get(self, asset_id: str):
        local = self.local.get(asset_id)
        return local if local is not None else self.official.get(asset_id)

    def prepare(self, asset_id: str) -> Path:
        local = self.local.get(asset_id)
        if local is not None:
            return self.local.prepare(asset_id)
        return self.official.prepare(asset_id)

    def sync(self):
        return self.official.sync()
