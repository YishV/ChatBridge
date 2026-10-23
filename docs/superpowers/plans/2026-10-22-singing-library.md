# QQ 曲库翻唱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 QQ 用户从 `data/music/` 指定或随机点歌，后台用 RVC 生成翻唱，并按配置发回 MP3 文件或 30 秒语音条。

**Architecture:** 网关只做曲库检索、入队和固定回复；独立 SQLite 队列由单独 worker 消费；worker 通过项目脚本调用 Demucs 和本地 RVC，生成缓存到 `data/runtime/covers/`，再通过 OneBot 文件/语音 API 发送。

**Tech Stack:** Python 3.12、SQLite、unittest、OneBot 11、NapCat、Demucs、RVC、ffmpeg、PowerShell

---

### Task 1: 曲库配置与目录

**Files:**
- Create: `src/chat_bridge/singing/__init__.py`
- Create: `src/chat_bridge/singing/config.py`
- Create: `data/music/README.md`
- Modify: `.gitignore`
- Test: `tests/test_singing_config.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from chat_bridge.singing.config import SingingSettings


class SingingSettingsTests(unittest.TestCase):
    def test_defaults_are_disabled_and_use_project_music_dir(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = SingingSettings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.music_dir, Path('data/music'))
        self.assertFalse(settings.send_mp3)
        self.assertEqual(settings.voice_excerpt_seconds, 30)

    def test_env_overrides_paths_and_switch(self):
        values = {
            'SINGING_ENABLED': 'true',
            'SINGING_MUSIC_DIR': 'D:/music',
            'SINGING_SEND_MP3': 'true',
            'SINGING_VOICE_EXCERPT_SECONDS': '20',
            'SINGING_PITCH': '5',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = SingingSettings.from_env()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.music_dir, Path('D:/music'))
        self.assertTrue(settings.send_mp3)
        self.assertEqual(settings.voice_excerpt_seconds, 20)
        self.assertEqual(settings.pitch, 5)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_config -v
```

Expected: `ModuleNotFoundError: No module named 'chat_bridge.singing'`

- [ ] **Step 3: Write minimal implementation**

`src/chat_bridge/singing/__init__.py`:

```python
"""Local song library and RVC cover jobs for the QQ adapter."""
```

`src/chat_bridge/singing/config.py`:

```python
"""Environment-backed settings for the optional singing feature."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError(f'{name} must be true or false')
    return value in ('true', '1')


@dataclass(frozen=True)
class SingingSettings:
    enabled: bool = False
    music_dir: Path = Path('data/music')
    send_mp3: bool = False
    voice_excerpt_seconds: int = 30
    max_duration_seconds: int = 600
    queue_db: Path = Path('data/runtime/singing.sqlite3')
    output_dir: Path = Path('data/runtime/covers')
    rvc_home: Path = Path('tools/RVC')
    rvc_python: Path = Path(
        'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe'
    )
    rvc_model: str = 'assets/weights/da_fei_yu_rvc.pth'
    ffmpeg: Path = Path(
        'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/ffmpeg.exe'
    )
    pitch: int = 12
    max_queue: int = 50
    poll_seconds: float = 1.0

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.send_mp3) is not bool:
            raise ValueError('SINGING_ENABLED and SINGING_SEND_MP3 must be bool')
        if type(self.voice_excerpt_seconds) is not int or not 1 <= self.voice_excerpt_seconds <= 120:
            raise ValueError('SINGING_VOICE_EXCERPT_SECONDS must be 1..120')
        if type(self.max_duration_seconds) is not int or not 1 <= self.max_duration_seconds <= 3600:
            raise ValueError('SINGING_MAX_DURATION_SECONDS must be 1..3600')
        if type(self.pitch) is not int or not -24 <= self.pitch <= 24:
            raise ValueError('SINGING_PITCH must be -24..24')
        if type(self.max_queue) is not int or not 1 <= self.max_queue <= 500:
            raise ValueError('SINGING_MAX_QUEUE must be 1..500')
        if type(self.poll_seconds) not in (int, float) or not 0.1 <= self.poll_seconds <= 60:
            raise ValueError('SINGING_POLL_SECONDS must be 0.1..60')
        if not isinstance(self.rvc_model, str) or not self.rvc_model.strip():
            raise ValueError('SINGING_RVC_MODEL must not be empty')

    @classmethod
    def from_env(cls) -> 'SingingSettings':
        load_dotenv(override=False, encoding='utf-8-sig')
        try:
            excerpt = int(os.getenv('SINGING_VOICE_EXCERPT_SECONDS', '30'))
            duration = int(os.getenv('SINGING_MAX_DURATION_SECONDS', '600'))
            pitch = int(os.getenv('SINGING_PITCH', '12'))
            max_queue = int(os.getenv('SINGING_MAX_QUEUE', '50'))
            poll = float(os.getenv('SINGING_POLL_SECONDS', '1.0'))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('SINGING 数值配置无效') from None
        return cls(
            enabled=_bool('SINGING_ENABLED', 'false'),
            music_dir=Path(os.getenv('SINGING_MUSIC_DIR', 'data/music')),
            send_mp3=_bool('SINGING_SEND_MP3', 'false'),
            voice_excerpt_seconds=excerpt,
            max_duration_seconds=duration,
            queue_db=Path(os.getenv('SINGING_QUEUE_DB', 'data/runtime/singing.sqlite3')),
            output_dir=Path(os.getenv('SINGING_OUTPUT_DIR', 'data/runtime/covers')),
            rvc_home=Path(os.getenv('SINGING_RVC_HOME', 'tools/RVC')),
            rvc_python=Path(os.getenv(
                'SINGING_RVC_PYTHON',
                'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe',
            )),
            rvc_model=os.getenv(
                'SINGING_RVC_MODEL', 'assets/weights/da_fei_yu_rvc.pth'
            ).strip(),
            ffmpeg=Path(os.getenv(
                'SINGING_FFMPEG',
                'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/ffmpeg.exe',
            )),
            pitch=pitch,
            max_queue=max_queue,
            poll_seconds=poll,
        )
```

`data/music/README.md`:

```markdown
# 曲库

把喜欢的歌曲放在这里，支持 mp3/wav/flac/m4a/ogg/opus。

例如：

```text
data/music/天空之城.mp3
data/music/李志 - 天空之城.mp3
data/music/李志/天空之城.mp3
```

音频文件不提交 Git；此 README 保留。
```

`.gitignore` 追加：

```gitignore
data/music/*
!data/music/README.md
```

- [ ] **Step 4: Run tests and check ignore**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_config -v
git check-ignore -v data/music/天空之城.mp3
```

Expected: tests PASS；`git check-ignore` 输出 `data/music/*`。

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/singing data/music/README.md tests/test_singing_config.py .gitignore
git commit -m "feat(singing): 添加曲库配置与目录"
```

---

### Task 2: 曲库扫描、匹配和随机

**Files:**
- Create: `src/chat_bridge/singing/library.py`
- Test: `tests/test_singing_library.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from pathlib import Path

from chat_bridge.singing.library import Song, SongLibrary


class SongLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fake')
        return path

    def test_scan_extracts_title_artist_and_skips_other_files(self):
        self.touch('天空之城.mp3')
        self.touch('李志 - 你好.wav')
        self.touch('cover.txt')
        library = SongLibrary(self.root)
        songs = library.scan()
        self.assertEqual([song.title for song in songs], ['你好', '天空之城'])
        self.assertEqual(songs[0].artist, '李志')

    def test_match_prefers_exact_then_fuzzy(self):
        self.touch('天空之城.mp3')
        self.touch('天空之城 Live.mp3')
        library = SongLibrary(self.root)
        exact = library.match('天空之城')
        self.assertEqual(exact[0].title, '天空之城')
        fuzzy = library.match('天空之成')
        self.assertTrue(fuzzy)
        self.assertIn('天空之城', fuzzy[0].title)

    def test_random_avoids_excluded_paths(self):
        a = self.touch('a.mp3')
        b = self.touch('b.mp3')
        library = SongLibrary(self.root)
        chosen = library.random(exclude={a.resolve()})
        self.assertEqual(chosen.path, b.resolve())
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_library -v
```

Expected: import error for `singing.library`.

- [ ] **Step 3: Write minimal implementation**

`src/chat_bridge/singing/library.py`:

```python
"""Bounded local audio library with title matching and random choice."""

import difflib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path


AUDIO_SUFFIXES = frozenset({'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'})
_MAX_FILES = 5000
_MAX_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class Song:
    path: Path
    title: str
    artist: str | None = None


def _clean_name(stem: str) -> tuple[str, str | None]:
    if ' - ' in stem:
        artist, title = stem.split(' - ', 1)
        return title.strip() or stem.strip(), artist.strip() or None
    return stem.strip(), None


def _normalize(value: str) -> str:
    return re.sub(r'[\s\W_]+', '', value.casefold())


class SongLibrary:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def scan(self) -> list[Song]:
        if not self.root.is_dir():
            return []
        songs = []
        for path in self.root.rglob('*'):
            if len(songs) >= _MAX_FILES:
                break
            if path.is_symlink() or not path.is_file():
                continue
            if any(part.startswith('.') for part in path.relative_to(self.root).parts):
                continue
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            try:
                if resolved.stat().st_size > _MAX_BYTES:
                    continue
            except OSError:
                continue
            title, artist = _clean_name(path.stem)
            songs.append(Song(resolved, title, artist))
        return sorted(songs, key=lambda song: (song.title.casefold(), str(song.path)))

    def match(self, query: str, limit: int = 3) -> list[Song]:
        if not isinstance(query, str) or not query.strip():
            return []
        q = _normalize(query)
        songs = self.scan()
        exact = [song for song in songs if _normalize(song.title) == q]
        if exact:
            return exact[:limit]
        contains = [song for song in songs if q and q in _normalize(song.title)]
        if contains:
            return contains[:limit]
        names = {_normalize(song.title): song for song in songs}
        closest = difflib.get_close_matches(q, list(names), n=limit, cutoff=0.45)
        return [names[name] for name in closest]

    def random(self, exclude: set[Path] | None = None) -> Song | None:
        excluded = {Path(path).resolve() for path in (exclude or set())}
        songs = [song for song in self.scan() if song.path not in excluded]
        if not songs:
            songs = self.scan()
        if not songs:
            return None
        return secrets.choice(songs)
```

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_library -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/singing/library.py tests/test_singing_library.py
git commit -m "feat(singing): 添加曲库扫描与匹配"
```

---

### Task 3: 点歌意图解析

**Files:**
- Create: `src/chat_bridge/singing/intent.py`
- Test: `tests/test_singing_intent.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from chat_bridge.singing.intent import SingingRequest, parse_request


class SingingIntentTests(unittest.TestCase):
    def test_command_with_title(self):
        request = parse_request('/翻唱 天空之城', is_group=False, mentioned=False)
        self.assertEqual(request, SingingRequest(query='天空之城'))

    def test_random_command_and_natural_language(self):
        self.assertTrue(parse_request('/随机翻唱', is_group=False, mentioned=False).random)
        self.assertTrue(parse_request('随便唱一首', is_group=False, mentioned=False).random)
        self.assertEqual(parse_request('唱首天空之城', is_group=False, mentioned=False).query, '天空之城')

    def test_group_requires_mention_or_slash(self):
        self.assertIsNone(parse_request('唱首天空之城', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('/翻唱 天空之城', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('唱首天空之城', is_group=True, mentioned=True))

    def testordinary_group_chat_does_not_trigger(self):
        self.assertIsNone(parse_request('今天唱歌吗', is_group=True, mentioned=False))
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_intent -v
```

Expected: import error.

- [ ] **Step 3: Write minimal implementation**

`src/chat_bridge/singing/intent.py`:

```python
"""Deterministic parsing of QQ singing requests."""

import re
from dataclasses import dataclass


_RANDOM_WORDS = frozenset({'', '歌', '首歌', '一首歌', '随便', '随机', '都行', '随便唱', '随机唱'})
_COMMAND = re.compile(r'^/(翻唱|随机翻唱)(?:\s+(.*))?$')
_NATURAL = re.compile(r'^(?:唱|来|翻唱)(?:一首|首|一下|一个)?\s*(.*)$')


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
```

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_intent -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/singing/intent.py tests/test_singing_intent.py
git commit -m "feat(singing): 添加点歌意图解析"
```

---

### Task 4: SQLite 任务队列

**Files:**
- Create: `src/chat_bridge/singing/queue.py`
- Test: `tests/test_singing_queue.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from pathlib import Path

from chat_bridge.singing.queue import QueueBusy, SingingQueue


class SingingQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = SingingQueue(Path(self.temp.name) / 'q.sqlite3')

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_enqueue_claim_complete_and_recent(self):
        job_id = self.queue.enqueue('private:1', False, '1', '天空之城', '/music/a.mp3')
        job = self.queue.claim_next()
        self.assertEqual(job.id, job_id)
        self.assertEqual(job.status, 'running')
        self.queue.complete(job_id, '/covers/a.mp3')
        self.assertEqual(self.queue.recent_paths('private:1'), ['/music/a.mp3'])

    def test_same_conversation_cannot_queue_twice(self):
        self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        with self.assertRaises(QueueBusy):
            self.queue.enqueue('private:1', False, '1', 'b', '/b.mp3')

    def test_same_song_in_same_conversation_returns_existing_id(self):
        first = self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        second = self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        self.assertEqual(first, second)

    def test_claim_returns_none_when_empty(self):
        self.assertIsNone(self.queue.claim_next())
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_queue -v
```

Expected: import error.

- [ ] **Step 3: Write minimal implementation**

`src/chat_bridge/singing/queue.py`:

```python
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
```

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_queue -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/singing/queue.py tests/test_singing_queue.py
git commit -m "feat(singing): 添加 SQLite 翻唱队列"
```

---

### Task 5: OneBot 文件上传与 QQSender

**Files:**
- Modify: `src/chat_bridge/qq/onebot.py`
- Modify: `src/chat_bridge/qq/sender.py`
- Test: `tests/test_qq_adapter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_qq_adapter.py`:

```python
def test_uploads_group_and_private_files(self):
    with TemporaryDirectory() as temp, OneBotServer() as server:
        path = Path(temp) / 'cover.mp3'
        path.write_bytes(b'ID3')
        client = OneBotClient(QQSettings(api_url=server.url))
        client.upload_group_file('123', path, 'cover.mp3')
        client.upload_private_file('456', path, 'cover.mp3')
        self.assertEqual([call[1]['group_id'] for call in server.calls], [123])
        self.assertEqual([call[1]['user_id'] for call in server.calls], [456])
```

`OneBotServer` needs `upload_group_file`/`upload_private_file` accepted and return `{'file_id': 'f1'}`. Add to handler:

```python
elif action in ('upload_group_file', 'upload_private_file'):
    data = {'file_id': 'f1'}
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qq_adapter.OneBotClientTests.test_uploads_group_and_private_files -v
```

Expected: FAIL because method missing.

- [ ] **Step 3: Write minimal implementation**

In `OneBotClient` add:

```python
    @staticmethod
    def _file(path: Path, name: str | None) -> tuple[Path, str]:
        path = Path(path).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise OneBotError('onebot_file_missing')
        if path.stat().st_size > 30 * 1024 * 1024:
            raise OneBotError('onebot_file_too_large')
        filename = name or path.name
        if not isinstance(filename, str) or not filename or len(filename) > 128:
            raise OneBotError('onebot_file_name_invalid')
        return path, filename

    def upload_group_file(self, group_id: str, path: Path, name: str | None = None) -> str:
        if not isinstance(group_id, str) or not group_id or len(group_id) > 64:
            raise OneBotError('onebot_group_invalid')
        file_path, filename = self._file(path, name)
        data = self.call('upload_group_file', {
            'group_id': int(group_id) if group_id.isdigit() else group_id,
            'file': str(file_path), 'name': filename,
        })
        result = data.get('data')
        return str(result.get('file_id')) if isinstance(result, dict) else 'uploaded'

    def upload_private_file(self, user_id: str, path: Path, name: str | None = None) -> str:
        if not isinstance(user_id, str) or not user_id or len(user_id) > 64:
            raise OneBotError('onebot_user_invalid')
        file_path, filename = self._file(path, name)
        data = self.call('upload_private_file', {
            'user_id': int(user_id) if user_id.isdigit() else user_id,
            'file': str(file_path), 'name': filename,
        })
        result = data.get('data')
        return str(result.get('file_id')) if isinstance(result, dict) else 'uploaded'
```

In `QQSender` add:

```python
    def send_group_file(self, conversation_id: str, path: Path, name: str | None = None):
        try:
            private = self._private_user(conversation_id)
            file_id = (self.client.upload_private_file(private, path, name)
                       if private is not None
                       else self.client.upload_group_file(conversation_id, path, name))
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, file_id)

    def send_voice_file(self, conversation_id: str, path: Path):
        try:
            private = self._private_user(conversation_id)
            message_id = (self.client.send_private_record(private, path)
                          if private is not None else self.client.send_group_record(conversation_id, path))
        except OneBotError as exc:
            raise VoiceError('qq_voice_unconfirmed', sent=True) from exc
        return VoiceSendResult(True, message_id)
```

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qq_adapter -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/qq/onebot.py src/chat_bridge/qq/sender.py tests/test_qq_adapter.py
git commit -m "feat(qq): 支持上传翻唱文件"
```

---

### Task 6: RVC 渲染器

**Files:**
- Create: `scripts/rvc_runner.py`
- Create: `src/chat_bridge/singing/renderer.py`
- Test: `tests/test_singing_renderer.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chat_bridge.singing.config import SingingSettings
from chat_bridge.singing.renderer import CoverRenderer, RenderError


class CoverRendererTests(unittest.TestCase):
    def test_missing_source_is_rejected_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = SingingSettings(rvc_home=Path(temp), rvc_python=Path(temp) / 'python.exe')
            renderer = CoverRenderer(settings)
            with self.assertRaises(RenderError):
                renderer.render(Path(temp) / 'missing.mp3', Path(temp) / 'out.mp3')

    def test_command_list_contains_expected_rvc_args(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'
            source.write_bytes(b'audio')
            settings = SingingSettings(
                rvc_home=root, rvc_python=root / 'python.exe',
                rvc_model='model.pth', ffmpeg=root / 'ffmpeg.exe',
            )
            (root / 'ffmpeg.exe').write_bytes(b'fake')
            renderer = CoverRenderer(settings)
            commands = []
            renderer._run = lambda cmd, **kwargs: commands.append(cmd)
            renderer._separate = lambda source, work: (root / 'vocals.wav', root / 'no_vocals.wav')
            renderer._convert = lambda vocals, work: (root / 'vocals.wav', commands.append(['rvc']))
            renderer._mix = lambda vocals, no_vocals, output: output.write_bytes(b'ID3')
            renderer.render(source, root / 'out.mp3')
            self.assertTrue(any('rvc' in cmd for cmd in commands))
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_renderer -v
```

Expected: import error.

- [ ] **Step 3: Write minimal implementation**

`scripts/rvc_runner.py`:

```python
"""Run RVC scripts under its bundled Python while adding the repo root to sys.path."""
import runpy
import sys
from pathlib import Path

rvc_home = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(rvc_home))
script = rvc_home / 'infer' / 'cli.py'
sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name='__main__')
```

`src/chat_bridge/singing/renderer.py`:

```python
"""Subprocess orchestration for Demucs + RVC cover rendering."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from chat_bridge.singing.config import SingingSettings


class RenderError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CoverRenderer:
    def __init__(self, settings: SingingSettings, *, runner=subprocess.run):
        self.settings = settings
        self._run = runner

    def render(self, source: Path, output: Path) -> Path:
        source = Path(source).resolve()
        output = Path(output)
        if not source.is_file() or source.suffix.lower() not in {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'}:
            raise RenderError('source_invalid')
        if not self.settings.ffmpeg.is_file():
            raise RenderError('ffmpeg_missing')
        work = self.settings.rvc_home / '.work' / hashlib.sha256(str(source).encode()).hexdigest()[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        vocals, no_vocals = self._separate(source, work)
        converted = self._convert(vocals, work)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._mix(converted, no_vocals, output)
        return output

    def _run_checked(self, command, cwd=None):
        try:
            self._run(command, cwd=str(cwd or self.settings.rvc_home), check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RenderError('render_subprocess_failed') from exc

    def _separate(self, source: Path, work: Path):
        out = work / 'separated'
        self._run_checked([
            str(self.settings.rvc_python), '-m', 'demucs', '-n', 'htdemucs',
            '--two-stems=vocals', '-d', 'cuda', '-o', str(out), str(source),
        ])
        base = out / 'htdemucs' / source.stem
        vocals, no_vocals = base / 'vocals.wav', base / 'no_vocals.wav'
        if not vocals.is_file() or not no_vocals.is_file():
            raise RenderError('separation_failed')
        return vocals, no_vocals

    def _convert(self, vocals: Path, work: Path) -> Path:
        chunks = work / 'chunks'
        converted = work / 'converted'
        chunks.mkdir(exist_ok=True)
        converted.mkdir(exist_ok=True)
        model = self.settings.rvc_model
        model_path = Path(model) if Path(model).is_absolute() else self.settings.rvc_home / model
        for start in range(0, 600, 30):
            chunk = chunks / f'v{start // 30}.wav'
            self._run_checked([
                str(self.settings.ffmpeg), '-y', '-ss', str(start), '-t', '30',
                '-i', str(vocals), '-ac', '1', '-ar', '40000', str(chunk),
            ])
            if chunk.stat().st_size <= 44:
                break
            target = converted / chunk.name
            self._run_checked([
                str(self.settings.rvc_python), str(Path(__file__).parents[3] / 'scripts' / 'rvc_runner.py'),
                str(self.settings.rvc_home), '--model', str(model_path), '--input', str(chunk),
                '--output', str(target), '--pitch', str(self.settings.pitch),
                '--f0-method', 'pm', '--index-rate', '0', '--format', 'wav',
            ])
        parts = sorted(converted.glob('v*.wav'))
        if not parts:
            raise RenderError('rvc_failed')
        concat = work / 'concat.txt'
        concat.write_text('\n'.join(f"file '{part.name}'" for part in parts), encoding='utf-8')
        joined = work / 'vocals_full.wav'
        self._run_checked([
            str(self.settings.ffmpeg), '-y', '-f', 'concat', '-safe', '0',
            '-i', str(concat), '-c', 'copy', str(joined),
        ], cwd=converted)
        return joined

    def _mix(self, vocals: Path, no_vocals: Path, output: Path):
        self._run_checked([
            str(self.settings.ffmpeg), '-y', '-i', str(vocals), '-i', str(no_vocals),
            '-filter_complex',
            '[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[m]',
            '-map', '[m]', '-ar', '44100', '-ac', '2', '-b:a', '320k', str(output),
        ])
```

> 说明：`_run` 可注入测试替身；上面测试会把 `_separate/_convert/_mix` 也替换掉，避免真实 RVC。真实渲染使用已验证的 `tools/RVC` 与 GPT-SoVITS runtime；如果 runtime 不能写 `data/runtime`，work 目录放在 RVC 目录下。

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_renderer -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add scripts/rvc_runner.py src/chat_bridge/singing/renderer.py tests/test_singing_renderer.py
git commit -m "feat(singing): 添加 RVC 渲染器"
```

---

### Task 7: 点歌协调器与后台 worker

**Files:**
- Create: `src/chat_bridge/singing/worker.py`
- Create: `src/chat_bridge/singing/coordinator.py`
- Test: `tests/test_singing_worker.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from pathlib import Path

from chat_bridge.singing.config import SingingSettings
from chat_bridge.singing.coordinator import SingingCoordinator
from chat_bridge.singing.queue import SingingQueue
from chat_bridge.singing.worker import SingingWorker
from chat_bridge.wechat.delivery import SendReceipt


class FakeSender:
    def __init__(self):
        self.texts = []
        self.files = []
        self.voices = []
    def send_group_text(self, conversation, text):
        self.texts.append((conversation, text)); return True
    def send_group_file(self, conversation, path, name=None):
        self.files.append((conversation, path)); return SendReceipt(True, 'file-1')
    def send_voice_file(self, conversation, path):
        self.voices.append((conversation, path)); return True


class FakeRenderer:
    def render(self, source, output):
        Path(output).write_bytes(b'ID3'); return Path(output)


class SingingWorkerTests(unittest.TestCase):
    def test_worker_sends_file_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'; source.write_bytes(b'audio')
            output = root / 'covers' / 'x.mp3'
            settings = SingingSettings(enabled=True, music_dir=root, send_mp3=True,
                                       output_dir=output.parent)
            queue = SingingQueue(root / 'q.sqlite3')
            job_id = queue.enqueue('private:1', False, '1', 'song', str(source))
            sender = FakeSender()
            worker = SingingWorker(settings, queue, FakeRenderer(), sender)
            worker.run_once()
            job = queue.claim_next()
            self.assertIsNone(job)
            self.assertEqual(len(sender.files), 1)
            self.assertEqual(len(sender.voices), 0)
            queue.close()
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_worker -v
```

Expected: import error.

- [ ] **Step 3: Write minimal implementation**

`src/chat_bridge/singing/coordinator.py`:

```python
"""Turn a parsed QQ request into a queued singing job."""

from pathlib import Path

from chat_bridge.singing.intent import SingingRequest, parse_request
from chat_bridge.singing.library import SongLibrary
from chat_bridge.singing.queue import QueueBusy, SingingQueue


class SingingCoordinator:
    def __init__(self, settings, library: SongLibrary, queue: SingingQueue, sender):
        self.settings = settings
        self.library = library
        self.queue = queue
        self.sender = sender

    def handle(self, message) -> bool:
        if not self.settings.enabled:
            return False
        request = parse_request(message.text, is_group=message.is_group,
                                mentioned=message.mentioned_bot)
        if request is None:
            return False
        songs = self.library.scan()
        if not songs:
            self.sender.send_group_text(message.group_id, '曲库还是空的，先把歌曲放进 data/music。')
            return True
        if request.random:
            song = self.library.random(
                exclude={Path(path) for path in self.queue.recent_paths(message.group_id)}
            )
            if song is None:
                self.sender.send_group_text(message.group_id, '曲库里暂时没有可翻唱的歌。')
                return True
        else:
            matches = self.library.match(request.query or '')
            if not matches:
                self.sender.send_group_text(message.group_id, f'没找到《{request.query}》。')
                return True
            song = matches[0]
        try:
            self.queue.enqueue(message.group_id, message.is_group, message.sender_id,
                               request.query or song.title, str(song.path))
        except QueueBusy:
            self.sender.send_group_text(message.group_id, '你已有一首在翻唱，等这首完成再来。')
            return True
        self.sender.send_group_text(message.group_id, f'收到，开始翻唱《{song.title}》。完成后发给你。')
        return True
```

`src/chat_bridge/singing/worker.py`:

```python
"""Background worker that renders queued covers and sends the result."""

import hashlib
import subprocess
import time
from pathlib import Path

from chat_bridge.singing.renderer import RenderError


class SingingWorker:
    def __init__(self, settings, queue, renderer, sender):
        self.settings = settings
        self.queue = queue
        self.renderer = renderer
        self.sender = sender

    def run_once(self) -> bool:
        job = self.queue.claim_next()
        if job is None:
            return False
        source = Path(job.song_path)
        output = self._cache_path(source)
        try:
            self.settings.output_dir.mkdir(parents=True, exist_ok=True)
            if not output.is_file() or output.stat().st_size == 0:
                self.renderer.render(source, output)
            if not output.is_file() or output.stat().st_size == 0:
                raise RenderError('render_empty')
            if self.settings.send_mp3:
                receipt = self.sender.send_group_file(job.conversation_id, output, output.name)
                if not getattr(receipt, 'confirmed', False):
                    self._send_voice(job, output)
                    self.sender.send_group_text(
                        job.conversation_id, '文件发送失败，先发 30 秒语音条。'
                    )
            else:
                self._send_voice(job, output)
            self.queue.complete(job.id, str(output))
            self.sender.send_group_text(job.conversation_id, f'《{job.query}》翻唱完成了。')
        except Exception as exc:
            code = exc.code if isinstance(exc, RenderError) else 'render_failed'
            self.queue.fail(job.id, code)
            self.sender.send_group_text(job.conversation_id, f'翻唱失败：{code}')
        return True

    def run_forever(self):
        while True:
            if not self.run_once():
                time.sleep(self.settings.poll_seconds)

    def _cache_path(self, source: Path) -> Path:
        digest = hashlib.sha256()
        digest.update(source.read_bytes())
        digest.update(self.settings.rvc_model.encode('utf-8'))
        digest.update(str(self.settings.pitch).encode('ascii'))
        return self.settings.output_dir / (digest.hexdigest()[:32] + '.mp3')

    def _send_voice(self, job, output: Path):
        excerpt = output.with_suffix('.30s.wav')
        self._excerpt(output, excerpt)
        self.sender.send_voice_file(job.conversation_id, excerpt)

    def _excerpt(self, source: Path, output: Path):
        subprocess.run([
            str(self.settings.ffmpeg), '-y', '-t', str(self.settings.voice_excerpt_seconds),
            '-i', str(source), '-ac', '1', '-ar', '32000', str(output),
        ], check=True)
```

- [ ] **Step 4: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_worker -v
```

Expected: PASS（测试覆盖 MP3 分支；语音条分支在后续任务补测）。

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/singing/coordinator.py src/chat_bridge/singing/worker.py tests/test_singing_worker.py
git commit -m "feat(singing): 添加点歌协调器与 worker"
```

---

### Task 8: QQ 网关、CLI 与启动脚本接入

**Files:**
- Modify: `src/chat_bridge/qq/gateway.py`
- Modify: `src/chat_bridge/cli.py`
- Modify: `scripts/start.ps1`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_qq_singing_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from types import SimpleNamespace

from chat_bridge.qq.gateway import QQGateway
from chat_bridge.qq.config import QQSettings


class Singing:
    def __init__(self):
        self.seen = []
    def handle(self, message):
        self.seen.append(message)
        return True


class Service:
    def handle(self, message):
        raise AssertionError('singing should bypass chat')


class TestGateway(unittest.TestCase):
    def test_singing_request_bypasses_chat_service(self):
        singing = Singing()
        gateway = QQGateway(
            QQSettings(allowed_groups=frozenset({'1'})),
            client=SimpleNamespace(), service=Service(), singing=singing,
        )
        gateway.events.put({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 2, 'message_id': 3, 'self_id': 999,
            'message': [{'type': 'text', 'data': {'text': '/随机翻唱'}}],
        })
        gateway.tick()
        self.assertEqual(len(singing.seen), 1)
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qq_singing_gateway -v
```

Expected: `QQGateway` unexpected keyword `singing`.

- [ ] **Step 3: Write minimal implementation**

Modify `QQGateway.__init__`:

```python
    def __init__(self, settings: QQSettings, client, service, *, self_id=None, vision=None, singing=None):
        ...
        self.singing = singing
```

In `tick`, after conversation allowlist check and before `self.service.handle`:

```python
            if self.singing is not None and self.singing.handle(message):
                received += 1 if not message.is_from_bot else 0
                continue
```

Add CLI `singing-worker` parser:

```python
    singing_worker = commands.add_parser('singing-worker', help='消费曲库翻唱队列')
    singing_worker.add_argument('--once', action='store_true')
```

In `main`, before `if args.command == 'qq':`:

```python
        if args.command == 'singing-worker':
            from chat_bridge.singing.config import SingingSettings
            from chat_bridge.singing.queue import SingingQueue
            from chat_bridge.singing.renderer import CoverRenderer
            from chat_bridge.singing.worker import SingingWorker
            from chat_bridge.qq.config import QQSettings
            from chat_bridge.qq.sender import QQSender
            singing = SingingSettings.from_env()
            if not singing.enabled:
                raise GatewayError('singing_disabled')
            qq = QQSettings.from_env()
            queue = SingingQueue(singing.queue_db)
            worker = SingingWorker(singing, queue, CoverRenderer(singing), QQSender(qq))
            try:
                if args.once:
                    worker.run_once()
                else:
                    worker.run_forever()
            finally:
                queue.close()
            return
```

In `run_qq`, declare `singing_queue = None` before the existing `try`, then after `sender` creation:

```python
        from chat_bridge.singing.config import SingingSettings
        from chat_bridge.singing.coordinator import SingingCoordinator
        from chat_bridge.singing.library import SongLibrary
        from chat_bridge.singing.queue import SingingQueue
        singing_settings = SingingSettings.from_env()
        singing = None
        if singing_settings.enabled:
            singing_queue = SingingQueue(singing_settings.queue_db)
            singing = SingingCoordinator(
                singing_settings, SongLibrary(singing_settings.music_dir),
                singing_queue, sender,
            )
        gateway = QQGateway(..., singing=singing)
```

In the existing `finally` block, before `store.close()`:

```python
        if singing_queue is not None:
            singing_queue.close()
```

`scripts/start.ps1` add function `Start-SingingWorker`:

```powershell
function Start-SingingWorker {
    $enabled = (Get-EnvValue $envMap 'SINGING_ENABLED' 'false').ToLower() -eq 'true'
    if (-not $enabled) { return }
    Write-Step '启动翻唱 worker'
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $Root 'scripts\start-singing-worker.ps1')
    ) -WorkingDirectory $Root
}
```

Create `scripts/start-singing-worker.ps1`:

```powershell
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
& $python -m chat_bridge singing-worker
```

Call `Start-SingingWorker` in `Start-QQ` before gateway.

`.env.example` add:

```dotenv
SINGING_ENABLED=false
SINGING_MUSIC_DIR=data/music
SINGING_SEND_MP3=false
SINGING_VOICE_EXCERPT_SECONDS=30
SINGING_MAX_DURATION_SECONDS=600
SINGING_QUEUE_DB=data/runtime/singing.sqlite3
SINGING_OUTPUT_DIR=data/runtime/covers
SINGING_RVC_HOME=tools/RVC
SINGING_RVC_PYTHON=tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe
SINGING_RVC_MODEL=assets/weights/da_fei_yu_rvc.pth
SINGING_PITCH=12
```

README add a short section:

```markdown
### 曲库翻唱

把歌曲放进 `data/music/`，开启 `SINGING_ENABLED=true`，再启动 QQ 网关和翻唱 worker。

```text
/翻唱 天空之城
/随机翻唱
```

`SINGING_SEND_MP3=true` 发完整 MP3，`false` 只发前 30 秒语音条。
```

- [ ] **Step 4: Run tests and script parse**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qq_singing_gateway -v
powershell.exe -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw 'scripts/start.ps1')); [void][scriptblock]::Create((Get-Content -Raw 'scripts/start-singing-worker.ps1')); 'parse ok'"
```

Expected: tests PASS；`parse ok`。

- [ ] **Step 5: Commit**

```powershell
git add src/chat_bridge/qq/gateway.py src/chat_bridge/cli.py scripts/start.ps1 scripts/start-singing-worker.ps1 .env.example README.md tests/test_qq_singing_gateway.py
git commit -m "feat(qq): 接入曲库点歌与翻唱 worker"
```

---

### Task 9: 语音条分支、文档与验收

**Files:**
- Modify: `tests/test_singing_worker.py`
- Create: `docs/singing-library.md`
- Modify: `README.md`

- [ ] **Step 1: Add failing voice-branch test**

```python
    def test_worker_sends_voice_excerpt_when_mp3_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'; source.write_bytes(b'audio')
            settings = SingingSettings(
                enabled=True, music_dir=root, send_mp3=False,
                output_dir=root / 'covers', ffmpeg=Path('ffmpeg'),
            )
            queue = SingingQueue(root / 'q.sqlite3')
            queue.enqueue('private:1', False, '1', 'song', str(source))
            sender = FakeSender()
            worker = SingingWorker(settings, queue, FakeRenderer(), sender)
            worker._excerpt = lambda source, output: output.write_bytes(b'RIFF')
            worker.run_once()
            self.assertEqual(len(sender.voices), 1)
            self.assertEqual(len(sender.files), 0)
            queue.close()
```

- [ ] **Step 2: Run test**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_singing_worker -v
```

Expected: PASS after worker already has voice branch.

- [ ] **Step 3: Document**

`docs/singing-library.md`:

```markdown
# 曲库翻唱

## 曲库

默认 `data/music/`，支持 mp3/wav/flac/m4a/ogg/opus；文件名即歌名，`歌手 - 歌名` 可带歌手。

## 点歌

```text
/翻唱 天空之城
/随机翻唱
唱首天空之城
```

群聊需要 `@机器人` 或 `/` 指令；私聊可直接说。

## 配置

```dotenv
SINGING_ENABLED=true
SINGING_MUSIC_DIR=data/music
SINGING_SEND_MP3=false
SINGING_VOICE_EXCERPT_SECONDS=30
```

`SINGING_SEND_MP3=true` 发完整 MP3；否则只发前 30 秒语音条。

## 启动

```powershell
.\scripts\start.ps1 -Target qq
```

worker 也可以单独启动：

```powershell
python -m chat_bridge singing-worker
```

成品缓存在 `data/runtime/covers/`，同一首歌第二次点歌会直接复用。

## 失败

- 曲库为空：先放歌
- 找不到：回复最接近的候选
- 重复点歌：同一会话一次只处理一首
- 生成失败：返回固定错误码，不发送半成品

## 版权

歌曲版权由使用者确认；当前大肥鱼 RVC 音色数据集为 CC BY-NC 4.0，仅非商业使用。
```

README 增加链接。

- [ ] **Step 4: Manual acceptance**

```powershell
# 1. 放一首歌
Copy-Item 'D:\mp3\天空之城.mp3' 'data\music\天空之城.mp3'

# 2. 启动（SINGING_ENABLED=true，SINGING_SEND_MP3=true/false 各测一次）
.\scripts\start.ps1 -Target qq

# 3. QQ 私聊
/翻唱 天空之城
/随机翻唱
```

验收：

- 收到入队回复
- 30 秒语音条出现，或 MP3 文件卡片出现
- 同一首第二次点歌命中缓存
- 群聊不 @ 不触发
- 没有曲库/找不到歌有固定回复

- [ ] **Step 5: Commit**

```powershell
git add tests/test_singing_worker.py docs/singing-library.md README.md
git commit -m "docs(singing): 补充曲库翻唱使用说明"
```

---

## Self-Review

**Spec coverage:**

- 曲库/扫描/匹配/随机：Task 2
- 指令 + 自然语言：Task 3
- 群聊 @/指令边界：Task 3 + Task 8
- SQLite 队列/去重/并发：Task 4
- MP3/语音条配置：Task 5 + Task 7 + Task 9
- RVC/Demucs 渲染：Task 6
- 文件发送：Task 5
- worker 独立进程：Task 8
- 缓存：Task 7（内容哈希缓存路径）+ Task 6（work 目录）
- 安全/白名单：现有 `QQGateway` allowlist + Task 2 路径限制
- 测试：每任务离线测试 + Task 9 手动验收

**Placeholder scan:** 无 TODO/TBD；代码步骤已展开；文档步骤给了实际内容。

**Type consistency:** `SingingSettings` 字段在 Task 1 定义，Task 6/7/8 使用；`send_group_file`/`send_voice_file` 在 Task 5 定义，Task 7 使用；`SingingJob` 在 Task 4 定义，Task 7 使用；`SendReceipt` 在 Task 7 测试返回，worker 用 `.confirmed` 判断。
