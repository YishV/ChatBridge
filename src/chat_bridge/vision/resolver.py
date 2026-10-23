"""Resolve an exact authorized native message to a bounded in-memory image.

Only fixed account paths, the selected group's attachment directory and an
exact content-verified image attachment basename are eligible. No network,
media persistence, process inspection or unrelated-account key discovery.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice
from pathlib import Path

from chat_bridge.vision.media import MAX_INPUT_BYTES, VisionError, VisionImage, decode_image
from chat_bridge.wechat.errors import GatewayFatalError
from chat_bridge.wechat.native import _native_xml, _source_text


_HEX = re.compile(r'[a-fA-F0-9]{32}\Z')
_GIF_ATTACHMENT = (8 << 32) | 49
_MAX_IDS = 256
_MAX_PACKED = 64 * 1024


@dataclass(frozen=True)
class ImageReference:
    group_id: str = field(repr=False)
    sender_id: str = field(repr=False)
    shard: str = field(repr=False)
    local_id: int = field(repr=False)
    server_id: int = field(repr=False)
    create_time: int = field(repr=False)
    local_type: int = 3


def _safe_path(root: Path, path: Path) -> Path:
    """Refuse every link/reparse ancestor, including links within the root."""
    root, path = root.absolute(), path.absolute()
    if '..' in path.parts or not path.is_relative_to(root):
        raise VisionError('decode_failed')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise VisionError('decode_failed')
    if not path.resolve().is_relative_to(root.resolve()):
        raise VisionError('decode_failed')
    return path


def _blob_ids(value) -> list[str]:
    """Extract bounded ASCII digests and real protobuf length-16 fields."""
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) > _MAX_PACKED:
        return []
    data = bytes(value)
    result = []

    def add(candidate):
        if candidate not in result and len(result) < _MAX_IDS:
            result.append(candidate)

    for match in re.finditer(rb'(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', data):
        add(match[0].decode('ascii').lower())
        if len(result) >= _MAX_IDS:
            return result
    budget = [2048]

    def varint(block, position):
        number = 0
        for shift in range(0, 70, 7):
            if position >= len(block):
                raise ValueError()
            byte = block[position]
            position += 1
            number |= (byte & 127) << shift
            if byte < 128:
                return number, position
        raise ValueError()

    def parse(block, depth):
        position = 0
        try:
            while position < len(block) and budget[0] > 0 and len(result) < _MAX_IDS:
                budget[0] -= 1
                tag, position = varint(block, position)
                if tag >> 3 == 0:
                    return
                wire = tag & 7
                if wire == 0:
                    _, position = varint(block, position)
                elif wire in (1, 5):
                    position += 8 if wire == 1 else 4
                elif wire == 2:
                    length, position = varint(block, position)
                    end = position + length
                    if end > len(block):
                        return
                    field_bytes = block[position:end]
                    if length == 16:
                        add(field_bytes.hex())
                    elif depth < 8 and length:
                        parse(field_bytes, depth + 1)
                    position = end
                else:
                    return
        except ValueError:
            return
    parse(data, 0)
    return result


def _basename(value: str) -> bool:
    return bool(value and len(value) <= 240 and value not in ('.', '..')
                and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value)
                and not value.endswith((' ', '.'))
                and not re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)', value))


def _message_crypto(root, digest):
    """Read a key and at most one alternate hash bound to this message MD5."""
    values = root.findall('appmsg/appattach/emojiinfo')
    if len(values) != 1 or len(values[0]) or values[0].attrib:
        return {}
    text = (values[0].text or '').strip()
    if not text or len(text) > _MAX_PACKED:
        return {}
    try:
        data = base64.b64decode(text, validate=True)
        position, fields = 0, {}

        def integer():
            nonlocal position
            number = 0
            for shift in range(0, 70, 7):
                if position >= len(data):
                    raise ValueError()
                byte = data[position]
                position += 1
                number |= (byte & 127) << shift
                if byte < 128:
                    return number
            raise ValueError()

        for _ in range(256):
            if position == len(data):
                break
            tag = integer()
            number, wire = tag >> 3, tag & 7
            if number == 0:
                return {}
            if wire == 0:
                integer()
            elif wire in (1, 5):
                position += 8 if wire == 1 else 4
            elif wire == 2:
                length = integer()
                end = position + length
                if end > len(data):
                    return {}
                if number in (1, 6, 9):
                    if number in fields:
                        return {}
                    fields[number] = data[position:end].decode('ascii')
                position = end
            else:
                return {}
        if position != len(data) or fields.get(1, '').lower() != digest:
            return {}
        result = {}
        if re.fullmatch(r'(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{64})', fields.get(6, '')):
            result['key'] = bytes.fromhex(fields[6])
        if _HEX.fullmatch(fields.get(9, '')):
            result['external'] = fields[9].lower()
        return result
    except (ValueError, UnicodeError):
        return {}


class NativeImageResolver:
    def __init__(self, reader, config, *, health_check=None):
        self.reader = reader
        self.config = config
        self.health_check = health_check

    def resolve(self, reference: ImageReference) -> VisionImage:
        try:
            return self._resolve(reference)
        except (VisionError, GatewayFatalError):
            raise
        except Exception:
            # Never propagate native IDs, decrypted XML, database paths or keys.
            raise VisionError('decode_failed') from None

    def _resolve(self, reference):
        if self.health_check is not None and self.health_check() is False:
            raise VisionError('decode_failed')
        if (not isinstance(reference, ImageReference)
                or reference.group_id not in self.config.groups
                or reference.group_id not in self.reader.groups
                or not reference.group_id.endswith('@chatroom')
                or not reference.sender_id
                or not re.fullmatch(r'message_\d+\.db', reference.shard)
                or any(type(value) is not int or value <= 0 for value in
                       (reference.local_id, reference.server_id, reference.create_time))
                or reference.local_type not in (3, _GIF_ATTACHMENT)):
            raise VisionError('decode_failed')
        account = Path(self.reader.account_path)
        _safe_path(Path(self.config.sandbox_root), account)
        table = self.reader._table(reference.group_id)
        expected = 'Msg_' + hashlib.md5(reference.group_id.encode()).hexdigest()
        if table != expected:
            raise VisionError('decode_failed')
        message = _safe_path(account, account / 'db_storage/message' / reference.shard)
        connection = self.reader._connect(message)
        try:
            connection.execute('PRAGMA query_only=ON')
            row = connection.execute(
                f'SELECT n.user_name,m.local_type,m.message_content FROM "{table}" m '
                'JOIN Name2Id n ON n.rowid=m.real_sender_id '
                'WHERE m.local_id=? AND m.server_id=? AND m.create_time=? '
                'AND length(CAST(m.message_content AS BLOB))<=65536 LIMIT 2',
                (reference.local_id, reference.server_id, reference.create_time)).fetchall()
        finally:
            connection.close()
        if len(row) != 1 or row[0][0] != reference.sender_id or row[0][1] != reference.local_type:
            raise VisionError('decode_failed')
        text = _source_text(row[0][2])
        if text is None:
            raise VisionError('decode_failed')
        text = re.sub(r'^' + re.escape(reference.sender_id) + r':\s*\n', '', text, count=1)
        root = _native_xml(text)
        if root is None or root.tag != 'msg':
            raise VisionError('decode_failed')
        ids, content_md5, title = self._metadata(root, reference.local_type)
        crypto = _message_crypto(root, content_md5) if content_md5 else {}
        key = crypto.get('key')
        content_digests = tuple(dict.fromkeys(filter(None, (content_md5, crypto.get('external')))))
        ids.extend(value for value in content_digests if value not in ids)
        # Exact file attachments are content-addressed before any decoding.
        months = self._months(reference.create_time)
        if title:
            file = _safe_path(account, account / 'msg/file' / months[0] / title)
            if file.is_file():
                raw = self._read(file)
                return self._decode_bound(raw, content_digests, key)
        if content_md5:
            # Fixed shared GIF caches require the exact message's content hash;
            # no directory enumeration or filename-only authorization.
            bases = [account / 'business/emoticon' / name for name in ('Persist', 'Thumb', 'Temp')]
            bases.extend(account / 'cache' / month / 'Emoticon' for month in months)
            for base in bases:
                _safe_path(account, base)
                if not base.is_dir():
                    continue
                for digest in content_digests:
                    for folder in (base / digest[:2], base):
                        for suffix in ('', '.gif', '.png', '.webp', '.dat'):
                            file = _safe_path(account, folder / (digest + suffix))
                            if not file.is_file():
                                continue
                            try:
                                return self._decode_bound(self._read(file), content_digests, key,
                                                          seeds=self._seeds())
                            except VisionError:
                                continue
        ids = list(dict.fromkeys([*ids, *self._resource_ids(account, reference)]))[:_MAX_IDS]
        attach = account / 'msg/attach' / hashlib.md5(reference.group_id.encode()).hexdigest()
        _safe_path(account, attach)
        seeds = None
        for month in months:
            directory = _safe_path(attach, attach / month / 'Img')
            if not directory.is_dir():
                continue
            for file_id in ids:
                for suffix in ('.dat', '_h.dat', '_t.dat'):
                    file = _safe_path(attach, directory / (file_id + suffix))
                    if not file.is_file():
                        continue
                    raw = self._read(file)
                    if seeds is None:
                        seeds = self._seeds()
                    try:
                        return self._decode_bound(raw, content_digests, key, seeds=seeds)
                    except VisionError:
                        continue
        raise VisionError('decode_failed')

    def _decode_bound(self, raw, digest, key, *, seeds=()):
        try:
            image = decode_image(raw, account_id=self.config.account_id, seeds=seeds)
            if not digest or image.source_md5 in digest:
                return image
        except VisionError:
            pass
        if key is not None and raw and len(raw) % 16 == 0:
            from Crypto.Cipher import AES
            for iv in (key[:16], bytes(16)):
                candidate = AES.new(key, AES.MODE_CBC, iv).decrypt(raw)
                padding = candidate[-1]
                if not 1 <= padding <= 16 or candidate[-padding:] != bytes([padding]) * padding:
                    continue
                candidate = candidate[:-padding]
                if hashlib.md5(candidate).hexdigest() not in digest:
                    continue
                image = decode_image(candidate, account_id=self.config.account_id)
                if image.source_md5 in digest:
                    return image
        raise VisionError('decode_failed')

    @staticmethod
    def _read(file):
        with file.open('rb') as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if not raw or len(raw) > MAX_INPUT_BYTES:
            raise VisionError('image_too_large')
        return raw

    @staticmethod
    def _months(created):
        now = datetime.fromtimestamp(created)
        index = now.year * 12 + now.month - 1
        return [f'{(index - offset) // 12:04d}-{(index - offset) % 12 + 1:02d}'
                for offset in range(24)]

    @staticmethod
    def _metadata(root, kind):
        if kind == 3:
            images = root.findall('img')
            if len(images) != 1:
                raise VisionError('decode_failed')
            ids = [images[0].get(name, '').lower() for name in ('md5', 'fileid')]
            return [value for value in ids if _HEX.fullmatch(value)], None, None
        apps = root.findall('appmsg')
        if len(apps) != 1:
            raise VisionError('decode_failed')
        app = apps[0]
        extension = (app.findtext('appattach/fileext') or '').strip().lower()
        title = (app.findtext('title') or '').strip()
        digests = [(app.findtext(path) or '').strip().lower() for path in
                   ('md5', 'appattach/filemd5', 'appattach/emoticonmd5',
                    'appattach/emojiinfo/emoticonmd5', 'emojiinfo/emoticonmd5')]
        digests.append((root.findtext('emojiinfo/emoticonmd5') or '').strip().lower())
        digests = list(dict.fromkeys(value for value in digests if _HEX.fullmatch(value)))
        if (app.findtext('type') != '8' or extension not in {'gif', 'png', 'jpg', 'jpeg', 'webp'}
                or title and (not _basename(title) or Path(title).suffix.lower() != '.' + extension)
                or len(digests) != 1):
            raise VisionError('decode_failed')
        return digests, digests[0], title

    def _resource_ids(self, account, reference):
        path = _safe_path(account, account / 'db_storage/message/message_resource.db')
        if not path.is_file():
            return []
        connection = self.reader._connect(path)
        try:
            connection.execute('PRAGMA query_only=ON')
            # Length is filtered inside SQLite, before fetching untrusted blobs.
            rows = connection.execute(
                'SELECT packed_info FROM MessageResourceInfo '
                'WHERE message_svr_id=? AND message_local_id=? AND message_create_time=? '
                'AND length(packed_info)<=65536 LIMIT 8',
                (reference.server_id, reference.local_id, reference.create_time)).fetchall()
            ids = []
            for (packed,) in rows:
                for value in _blob_ids(packed):
                    if value not in ids:
                        ids.append(value)
                    if len(ids) >= _MAX_IDS:
                        return ids
            return ids
        finally:
            connection.close()

    def _seeds(self):
        root = Path(self.config.sandbox_root)
        users = _safe_path(root, root / 'user')
        if not users.is_dir():
            return ()
        result = []
        with os.scandir(users) as entries:
            for user in islice(entries, 64):
                directory = _safe_path(root, Path(user.path) / 'AppData/Roaming/Tencent/xwechat/net/kvcomm')
                if not directory.is_dir():
                    continue
                with os.scandir(directory) as files:
                    for entry in islice(files, 256):
                        match = re.fullmatch(r'key_(\d{1,20})_.+\.statistic', entry.name)
                        if not match:
                            continue
                        _safe_path(root, Path(entry.path))
                        seed = int(match[1])
                        if seed <= 0xffffffffffffffff and seed not in result:
                            result.append(seed)
                        if len(result) == 32:
                            return tuple(result)
        return tuple(result)
