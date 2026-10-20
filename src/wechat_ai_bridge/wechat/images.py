"""Static GIF validation and exact native receipt matching."""

import hashlib
import io
import re
from pathlib import Path
from xml.etree import ElementTree

from wechat_ai_bridge.wechat.errors import GatewayError


def outgoing_directory(executable: Path) -> Path:
    """Match the existing secured component's fixed media directory."""
    try:
        return executable.resolve().parents[3] / 'state/stickers/outgoing'
    except IndexError:
        raise GatewayError('native_media_directory_invalid') from None


def static_gif(path: Path, *, directory: Path | None = None) -> dict:
    path = path.resolve()
    if directory is not None and path.parent != directory.resolve():
        raise GatewayError('native_media_outside_directory')
    if not re.fullmatch(r'[a-f0-9]{32}\.gif', path.name) or not path.is_file():
        raise GatewayError('native_media_file_invalid')
    try:
        with path.open('rb') as source:
            data = source.read(8*1024*1024 + 1)
        if not data or len(data) > 8*1024*1024:
            raise ValueError()
        digest = hashlib.md5(data).hexdigest()
        if digest != path.stem:
            raise ValueError()
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if (image.format != 'GIF' or getattr(image, 'n_frames', 1) != 1
                    or not 1 <= width <= 4096 or not 1 <= height <= 4096 or width*height > 16_000_000):
                raise ValueError()
            image.load()
        return {'md5': digest, 'len': len(data), 'width': width, 'height': height, 'type': 1, 'productid': ''}
    except (OSError, ValueError, ImportError):
        raise GatewayError('native_media_file_invalid') from None


def matches_image_receipt(row, digest: str) -> bool:
    """Accept only a native media field, never arbitrary text containing a hash."""
    kind = row.local_type & 0xffffffff
    if kind not in (3, 47, 49) or not isinstance(row.text, str):
        return False
    text = row.text
    if len(text.encode('utf-8')) > 65536 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        return False
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError):
        return False
    if root.tag != 'msg':
        return False
    if kind in (3, 47):
        element = root.find('img' if kind == 3 else 'emoji')
        return element is not None and element.get('md5', '').lower() == digest
    app = root.find('appmsg')
    if app is None or app.findtext('type') != '8':
        return False
    return any((app.findtext(field) or '').strip().lower() == digest
               for field in ('appattach/filemd5', 'appattach/md5', 'md5'))
