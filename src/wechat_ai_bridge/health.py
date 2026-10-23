"""Read-only status checks for the local operator panel."""

from __future__ import annotations

import os
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from wechat_ai_bridge.memory import MemoryStore
from wechat_ai_bridge.settings import Settings


def _lock_held(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        import msvcrt
        with path.open('a+b') as handle:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return False
    except (ImportError, OSError):
        return False


def _tcp(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _url_port(value: str, fallback: int) -> tuple[str, int]:
    try:
        parsed = urlparse(value)
        host = parsed.hostname or '127.0.0.1'
        port = parsed.port or fallback
        return host, int(port)
    except (ValueError, TypeError):
        return '127.0.0.1', fallback


def _queue_counts(path: Path) -> dict:
    if not path.is_file():
        return {'queued': 0, 'running': 0, 'done': 0, 'failed': 0}
    try:
        connection = sqlite3.connect(str(path), timeout=3)
        try:
            rows = connection.execute(
                'SELECT status, COUNT(*) FROM jobs GROUP BY status'
            ).fetchall()
            counts = {'queued': 0, 'running': 0, 'done': 0, 'failed': 0}
            for status, count in rows:
                if status in counts:
                    counts[status] = int(count)
            return counts
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return {'queued': 0, 'running': 0, 'done': 0, 'failed': 0}


def collect_health(settings: Settings) -> dict:
    qq_host, qq_port = _url_port(
        os.getenv('QQ_ONEBOT_API_URL', 'http://127.0.0.1:3000'), 3000
    )
    gpt_host, gpt_port = _url_port(
        os.getenv('QQ_GPT_SOVITS_URL', 'http://127.0.0.1:9880'), 9880
    )
    webhook_host = os.getenv('QQ_WEBHOOK_HOST', '127.0.0.1')
    try:
        webhook_port = int(os.getenv('QQ_WEBHOOK_PORT', '8081'))
    except ValueError:
        webhook_port = 8081
    memory_db = Path(settings.memory_db).resolve()
    queue_db = Path(os.getenv(
        'SINGING_QUEUE_DB', 'data/runtime/singing.sqlite3'
    )).resolve()
    memories = []
    try:
        memories = MemoryStore(memory_db).list_all(limit=500)
    except (OSError, ValueError):
        pass
    return {
        'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'ports': {
            'qq_webhook': _tcp(webhook_host, webhook_port),
            'napcat': _tcp(qq_host, qq_port),
            'gpt_sovits': _tcp(gpt_host, gpt_port),
        },
        'wechat_gateway': _lock_held(
            Path(settings.state_db).with_suffix('.gateway.lock')
        ),
        'memory': {
            'db': str(memory_db),
            'count': len(memories),
        },
        'singing_queue': _queue_counts(queue_db),
        'log_dir': str(Path(settings.log_dir).resolve()),
    }
