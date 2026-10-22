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
