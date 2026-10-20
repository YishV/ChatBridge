"""Public DeepSeek meme metadata and bounded, on-demand static GIF delivery.

The publisher's public repository is a separate permitted source when its CDN
denies access. No access-control workaround, chat text, or model key is used.
"""

import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request
import warnings
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4


ARCHIVE_URL = 'https://aigengtu.com/archive'
OFFICIAL_BASE = 'https://raw.githubusercontent.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack/main/'
README_URL = OFFICIAL_BASE + 'README.md'
CDN_BASE = 'https://img.aigengtu.com/meme/'
INDEX_LIMIT = 2 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
# These paths were verified in the publisher's README, not inferred mirror IDs.
OFFICIAL_IDS = frozenset('123 010 022 035 037 040 044 050 052 056 058 070 071 077 081 083 085 087 097'.split())


class MemeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9]{3,6}', value) is not None


@dataclass(frozen=True)
class MemeAsset:
    id: str
    description: str
    url: str
    fallback_url: str | None = None

    def __post_init__(self):
        if (not _valid_id(self.id) or not isinstance(self.description, str)
                or not self.description.strip() or len(self.description) > 500
                or self.url != CDN_BASE + self.id + '.webp'
                or self.fallback_url is not None and (
                    self.id not in OFFICIAL_IDS
                    or self.fallback_url != OFFICIAL_BASE + 'previews/' + self.id + '.webp')):
            raise MemeError('meme_asset_invalid')


def _asset(asset_id, description):
    fallback = OFFICIAL_BASE + 'previews/' + asset_id + '.webp' if asset_id in OFFICIAL_IDS else None
    return MemeAsset(asset_id, description.strip()[:500], CDN_BASE + asset_id + '.webp', fallback)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ArchiveParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.asset_id = None
        self.words = []
        self.assets = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a':
            match = re.fullmatch(r'(?:https://aigengtu\.com)?/meme/([0-9]{3,6})', values.get('href', ''))
            self.asset_id = match[1] if match else None
            self.words = []
        elif tag == 'img' and self.asset_id:
            self.words.append(values.get('alt', ''))

    def handle_data(self, data):
        if self.asset_id:
            self.words.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.asset_id:
            description = ' '.join(' '.join(self.words).split())
            if description:
                self.assets[self.asset_id] = _asset(self.asset_id, description)
            self.asset_id = None
            self.words = []


def _safe_directory(directory):
    directory = Path(directory).absolute()
    for part in (directory, *directory.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise MemeError('meme_path_invalid')
    return directory.resolve()


def _read_json(path, limit=INDEX_LIMIT):
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= limit:
        raise MemeError('meme_cache_invalid')
    return json.loads(path.read_bytes().decode('utf-8'))


def _atomic_write(path, data):
    if path.is_symlink():
        raise MemeError('meme_path_invalid')
    temporary = path.parent / ('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise MemeError('meme_pillow_missing') from None
    return Image


def _image(data, *, require_gif=False):
    Image = _pillow()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            frame = Image.open(io.BytesIO(data))
            if (getattr(frame, 'n_frames', 1) != 1
                    or getattr(frame, 'is_animated', False)
                    or min(frame.size) < 1 or max(frame.size) > 4096
                    or frame.width * frame.height > 16_000_000
                    or require_gif and frame.format != 'GIF'):
                raise MemeError('meme_image_invalid')
            frame.load()
            return frame
    except MemeError:
        raise
    except Exception:
        raise MemeError('meme_image_invalid') from None


class MemeCatalog:
    def __init__(self, root: Path, outgoing: Path):
        self.root = Path(root)
        self.outgoing = Path(outgoing)
        self.assets = ()
        self._load()

    def _load(self):
        try:
            root = _safe_directory(self.root)
            saved = _read_json(root / 'catalog.json')
            if (not isinstance(saved, dict) or saved.get('version') != 1
                    or saved.get('source') not in (ARCHIVE_URL, README_URL)
                    or not isinstance(saved.get('assets'), list)):
                return
            assets = tuple(MemeAsset(**item) for item in saved['assets'])
            if len({a.id for a in assets}) == len(assets):
                self.assets = assets
        except (OSError, ValueError, TypeError):
            self.assets = ()

    def _fetch(self, url, limit):
        allowed = url in (ARCHIVE_URL, README_URL) or re.fullmatch(
            r'https://img\.aigengtu\.com/meme/[0-9]{3,6}\.webp', url)
        if not allowed:
            allowed = any(url == OFFICIAL_BASE + 'previews/' + asset_id + '.webp' for asset_id in OFFICIAL_IDS)
        if not allowed:
            raise MemeError('meme_url_invalid')
        request = urllib.request.Request(url, headers={'User-Agent': 'wechat-ai-bridge-memes/1.0'})
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=5) as response:
                if response.geturl() != url:
                    raise MemeError('meme_redirect_forbidden')
                data = response.read(limit + 1)
            if len(data) > limit:
                raise MemeError('meme_download_too_large')
            return data
        except MemeError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise MemeError('meme_download_unavailable') from None

    def sync(self) -> dict:
        assets = ()
        source = ARCHIVE_URL
        try:
            parser = _ArchiveParser()
            parser.feed(self._fetch(ARCHIVE_URL, INDEX_LIMIT).decode('utf-8'))
            assets = tuple(parser.assets.values())
        except (MemeError, UnicodeError, ValueError):
            pass
        if not assets:
            source = README_URL
            try:
                text = self._fetch(README_URL, INDEX_LIMIT).decode('utf-8')
                items = {}
                for description, asset_id in re.findall(r'!\[([^\]\r\n]+)\]\(previews/([0-9]{3,6})\.webp\)', text):
                    if asset_id in OFFICIAL_IDS:
                        items[asset_id] = _asset(asset_id, description)
                assets = tuple(items.values())
            except (MemeError, UnicodeError, ValueError):
                raise MemeError('meme_index_unavailable') from None
        if not assets:
            raise MemeError('meme_index_unavailable')
        saved = {'version': 1, 'source': source, 'publisher': 'https://aigengtu.com/',
                 'assets': [asdict(asset) for asset in assets]}
        try:
            root = _safe_directory(self.root)
            root.mkdir(parents=True, exist_ok=True)
            _atomic_write(root / 'catalog.json', json.dumps(saved, ensure_ascii=False).encode('utf-8'))
        except OSError:
            raise MemeError('meme_cache_unavailable') from None
        self.assets = assets
        return {'count': len(assets), 'source': source}

    def get(self, asset_id) -> MemeAsset | None:
        if not _valid_id(asset_id):
            return None
        return next((asset for asset in self.assets if asset.id == asset_id), None)

    def candidates(self, text: str, *, excluded=(), limit=12) -> list[MemeAsset]:
        if type(limit) is not int or limit <= 0:
            return []
        limit = min(limit, 30)
        excluded = set(excluded)
        available = [asset for asset in self.assets if asset.id not in excluded]
        words = set(re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]', str(text).lower()[:4000])) - set('的了我你是就这那在有也吗啊呢吧')
        pairs = {str(text)[i:i + 2].lower() for i in range(min(len(str(text)) - 1, 4000))}
        def score(asset):
            description = asset.description.lower()
            return sum(3 for pair in pairs if pair in description) + sum(1 for word in words if word in description)
        ranked = sorted(available, key=lambda asset: (-score(asset), asset.id))
        if ranked and score(ranked[0]) > 0:
            return ranked[:limit]
        # Spread unrelated fallback choices over the catalog for model judgement.
        count = min(limit, len(ranked))
        return [ranked[i * len(ranked) // count] for i in range(count)] if count else []

    def _cached(self, asset_id, root, outgoing):
        try:
            saved = _read_json(root / 'cache.json')
            digest = saved.get(asset_id) if isinstance(saved, dict) else None
            if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{32}', digest) is None:
                return None
            path = outgoing / (digest + '.gif')
            if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= IMAGE_LIMIT:
                return None
            data = path.read_bytes()
            if hashlib.md5(data).hexdigest() != digest:
                return None
            with _image(data, require_gif=True):
                pass
            return path
        except (OSError, ValueError, TypeError):
            return None

    def prepare(self, asset_id) -> Path:
        asset = self.get(asset_id)
        if asset is None:
            raise MemeError('meme_unknown_id')
        try:
            root, outgoing = _safe_directory(self.root), _safe_directory(self.outgoing)
            cached = self._cached(asset.id, root, outgoing)
            if cached:
                return cached
            try:
                data = self._fetch(asset.url, IMAGE_LIMIT)
            except MemeError as exc:
                if exc.code != 'meme_download_unavailable' or not asset.fallback_url:
                    raise
                data = self._fetch(asset.fallback_url, IMAGE_LIMIT)
            Image = _pillow()
            with _image(data) as frame:
                rgba = frame.convert('RGBA')
                clean = Image.new('RGB', frame.size, 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
                buffer = io.BytesIO()
                clean.save(buffer, format='GIF')
                gif = buffer.getvalue()
            if len(gif) > IMAGE_LIMIT:
                raise MemeError('meme_image_invalid')
            digest = hashlib.md5(gif).hexdigest()
            root.mkdir(parents=True, exist_ok=True)
            outgoing.mkdir(parents=True, exist_ok=True)
            path = outgoing / (digest + '.gif')
            _atomic_write(path, gif)
            try:
                saved = _read_json(root / 'cache.json')
                saved = {key: value for key, value in saved.items()
                         if _valid_id(key) and isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value)}
            except (OSError, ValueError, TypeError, AttributeError):
                saved = {}
            saved[asset.id] = digest
            _atomic_write(root / 'cache.json', json.dumps(saved).encode('utf-8'))
            return path
        except MemeError:
            raise
        except OSError:
            raise MemeError('meme_cache_unavailable') from None
