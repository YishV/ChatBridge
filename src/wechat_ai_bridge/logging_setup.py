"""Small rotating file log for status and long-running services."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


_configured_path: Path | None = None


def configure_logging(directory: Path, level: str = 'INFO') -> Path:
    """Attach one rotating file handler to the root logger.

    Repeated CLI calls in the same process are idempotent. Chat text is never
    written here by the application; callers log ids, error codes and counts.
    """
    global _configured_path
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'app.log'
    if _configured_path == path:
        return path
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s'
    ))
    root.addHandler(handler)
    _configured_path = path
    return path


def tail_log(directory: Path, lines: int = 200) -> list[str]:
    path = Path(directory).resolve() / 'app.log'
    if not path.is_file():
        return []
    lines = max(1, min(int(lines), 2000))
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            return [line.rstrip('\n') for line in handle.readlines()[-lines:]]
    except OSError:
        return []
