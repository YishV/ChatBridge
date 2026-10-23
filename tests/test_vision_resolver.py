import hashlib
import base64
import io
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from chat_bridge.vision.media import VisionError
from chat_bridge.wechat.errors import GatewayFatalError
from chat_bridge.vision.resolver import ImageReference, NativeImageResolver, _blob_ids


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.account = Path(self.temp.name) / 'account'
        self.message_dir = self.account / 'db_storage/message'
        self.message_dir.mkdir(parents=True)
        self.group = 'synthetic@chatroom'
        self.table = 'Msg_' + hashlib.md5(self.group.encode()).hexdigest()
        self.reference = ImageReference(self.group, 'synthetic_sender', 'message_0.db', 7, 101, 1790000000)
        self.digest = 'a' * 32
        self.db = self.message_dir / 'message_0.db'
        with database(self.db) as connection:
            connection.execute('CREATE TABLE Name2Id(user_name TEXT)')
            connection.execute('INSERT INTO Name2Id VALUES (?)', (self.reference.sender_id,))
            connection.execute(f'CREATE TABLE "{self.table}"(local_id INTEGER, server_id INTEGER, create_time INTEGER, real_sender_id INTEGER, local_type INTEGER, message_content TEXT)')
            connection.execute(f'INSERT INTO "{self.table}" VALUES (7,101,1790000000,1,3,?)',
                               (f'<msg><img md5="{self.digest}"/></msg>',))
        self.opened = []
        def connect(path):
            self.opened.append(path)
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            connection.execute('PRAGMA query_only=ON')
            return connection
        self.reader = SimpleNamespace(account_path=self.account, account_id='synthetic_account',
                                      groups=frozenset({self.group}), _connect=connect,
                                      _table=lambda group: self.table)
        self.config = SimpleNamespace(groups=self.reader.groups, account_id=self.reader.account_id,
                                      sandbox_root=Path(self.temp.name))
        self.health = Mock()
        self.resolver = NativeImageResolver(self.reader, self.config, health_check=self.health)
        image = io.BytesIO()
        Image.new('RGB', (24, 16), 'red').save(image, 'PNG')
        self.raw = image.getvalue()

    def put_image(self, raw=None, digest=None):
        month = datetime.fromtimestamp(self.reference.create_time).strftime('%Y-%m')
        path = self.account / 'msg/attach' / hashlib.md5(self.group.encode()).hexdigest() / month / 'Img' / ((digest or self.digest) + '.dat')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.raw if raw is None else raw)
        return path

    def update_row(self, content, kind):
        with database(self.db) as connection:
            connection.execute(f'UPDATE "{self.table}" SET message_content=?,local_type=?', (content, kind))

    def make_gif_attachment(self, *, valid=True, title='test.gif'):
        image = io.BytesIO()
        Image.new('RGB', (20, 10), 'red').save(image, 'GIF', save_all=True,
            append_images=[Image.new('RGB', (20, 10), 'blue')])
        data = image.getvalue()
        digest = hashlib.md5(data).hexdigest() if valid else 'b' * 32
        kind = (8 << 32) | 49
        self.update_row(f'<msg><appmsg><type>8</type><title>{title}</title><md5>{digest}</md5><appattach><fileext>gif</fileext></appattach></appmsg></msg>', kind)
        month = datetime.fromtimestamp(self.reference.create_time).strftime('%Y-%m')
        file = self.account / 'msg/file' / month / 'test.gif'
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return replace(self.reference, local_type=kind)

    def test_exact_image_raw_and_legacy_xor(self):
        for raw in (self.raw, bytes(value ^ 0x9d for value in self.raw)):
            self.put_image(raw)
            result = self.resolver.resolve(self.reference)
            self.assertEqual((result.width, result.height), (24, 16))
        self.assertEqual(self.health.call_count, 2)

    def test_reference_repr_hides_identity(self):
        for value in (self.group, self.reference.sender_id, self.reference.shard, '101', '1790000000'):
            self.assertNotIn(value, repr(self.reference))

    def test_rejects_mismatched_identity_and_unselected_group(self):
        self.put_image()
        for change in ({'sender_id': 'other'}, {'server_id': 102}, {'local_id': 8},
                       {'create_time': 1790000001}, {'group_id': 'other@chatroom'},
                       {'local_type': 47}, {'shard': '../message_0.db'}):
            with self.subTest(change=change), self.assertRaises(VisionError):
                self.resolver.resolve(replace(self.reference, **change))
        self.config.groups = frozenset()
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    def test_health_failure_prevents_database_read(self):
        self.health.side_effect = RuntimeError('private health details')
        with self.assertRaises(VisionError) as error:
            self.resolver.resolve(self.reference)
        self.assertEqual(str(error.exception), 'decode_failed')
        self.assertEqual(self.opened, [])

    def test_fatal_native_identity_failure_propagates(self):
        self.health.side_effect = GatewayFatalError('native_account_or_version_mismatch')
        with self.assertRaises(GatewayFatalError):
            self.resolver.resolve(self.reference)

    def test_verified_sender_prefix_and_dtd_rejection(self):
        self.put_image()
        self.update_row(f'{self.reference.sender_id}:\n<msg><img md5="{self.digest}"/></msg>', 3)
        self.assertEqual(self.resolver.resolve(self.reference).width, 24)
        self.update_row(f'<!DOCTYPE msg><msg><img md5="{self.digest}"/></msg>', 3)
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    def test_missing_file_fails_without_other_group_search(self):
        file = self.put_image()
        other = file.parents[3] / 'other_group' / file.relative_to(file.parents[2])
        other.parent.mkdir(parents=True)
        file.replace(other)
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    def test_gif_attachment_requires_content_digest(self):
        reference = self.make_gif_attachment()
        result = self.resolver.resolve(reference)
        self.assertEqual((result.width, result.height), (20, 10))
        self.assertTrue(result.sampled_frame)
        reference = self.make_gif_attachment(valid=False)
        with self.assertRaises(VisionError):
            self.resolver.resolve(reference)

    def test_image_attachment_without_title_uses_exact_digest_cache(self):
        reference = self.make_gif_attachment()
        month = datetime.fromtimestamp(reference.create_time).strftime('%Y-%m')
        file = self.account / 'msg/file' / month / 'test.gif'
        raw = file.read_bytes()
        digest = hashlib.md5(raw).hexdigest()
        file.unlink()
        self.update_row(f'<msg><appmsg><type>8</type><title/><md5/><appattach><fileext>gif</fileext><emoticonmd5>{digest}</emoticonmd5></appattach></appmsg></msg>', reference.local_type)
        self.put_image(bytes(value ^ 0x9d for value in raw), digest)
        self.assertTrue(self.resolver.resolve(reference).sampled_frame)

    def test_gif_exact_digest_business_cache_and_content_validation(self):
        reference = self.make_gif_attachment()
        month = datetime.fromtimestamp(reference.create_time).strftime('%Y-%m')
        file = self.account / 'msg/file' / month / 'test.gif'
        raw = file.read_bytes()
        digest = hashlib.md5(raw).hexdigest()
        file.unlink()
        self.update_row(f'<msg><appmsg><type>8</type><appattach><fileext>gif</fileext><emoticonmd5>{digest}</emoticonmd5></appattach></appmsg></msg>', reference.local_type)
        cache = self.account / 'business/emoticon/Persist' / digest[:2] / digest
        cache.parent.mkdir(parents=True)
        cache.write_bytes(raw)
        self.assertTrue(self.resolver.resolve(reference).sampled_frame)
        cache.write_bytes(self.raw)
        with self.assertRaises(VisionError):
            self.resolver.resolve(reference)

    def test_message_bound_emojiinfo_cbc_cache(self):
        from Crypto.Cipher import AES
        reference = self.make_gif_attachment()
        month = datetime.fromtimestamp(reference.create_time).strftime('%Y-%m')
        file = self.account / 'msg/file' / month / 'test.gif'
        raw = file.read_bytes()
        digest = hashlib.md5(raw).hexdigest()
        file.unlink()
        key = bytes(range(16))
        encoded = base64.b64encode(b'\x0a\x20' + digest.encode() + b'\x32\x20' + key.hex().encode()).decode()
        xml = f'<msg><appmsg><type>8</type><appattach><fileext>gif</fileext><emoticonmd5>{digest}</emoticonmd5><emojiinfo>{encoded}</emojiinfo></appattach></appmsg></msg>'
        self.update_row(xml, reference.local_type)
        cache = self.account / 'business/emoticon/Persist' / digest[:2] / digest
        cache.parent.mkdir(parents=True)
        padding = 16 - len(raw) % 16
        for iv in (key, bytes(16)):
            cache.write_bytes(AES.new(key, AES.MODE_CBC, iv).encrypt(raw + bytes([padding]) * padding))
            self.assertTrue(self.resolver.resolve(reference).sampled_frame)
        encoded_wrong = base64.b64encode(b'\x0a\x20' + b'f' * 32 + b'\x32\x20' + key.hex().encode()).decode()
        self.update_row(xml.replace(encoded, encoded_wrong), reference.local_type)
        with self.assertRaises(VisionError):
            self.resolver.resolve(reference)

    def test_message_bound_platform_hash_cache(self):
        reference = self.make_gif_attachment()
        month = datetime.fromtimestamp(reference.create_time).strftime('%Y-%m')
        file = self.account / 'msg/file' / month / 'test.gif'
        raw = file.read_bytes()
        primary, external = 'e' * 32, hashlib.md5(raw).hexdigest()
        file.unlink()
        encoded = base64.b64encode(b'\x0a\x20' + primary.encode() + b'\x4a\x20' + external.encode()).decode()
        xml = f'<msg><appmsg><type>8</type><appattach><fileext>gif</fileext><emoticonmd5>{primary}</emoticonmd5><emojiinfo>{encoded}</emojiinfo></appattach></appmsg></msg>'
        self.update_row(xml, reference.local_type)
        cache = self.account / 'business/emoticon/Persist' / external[:2] / external
        cache.parent.mkdir(parents=True)
        cache.write_bytes(raw)
        self.assertTrue(self.resolver.resolve(reference).sampled_frame)
        wrong = base64.b64encode(b'\x0a\x20' + b'f' * 32 + b'\x4a\x20' + external.encode()).decode()
        self.update_row(xml.replace(encoded, wrong), reference.local_type)
        with self.assertRaises(VisionError):
            self.resolver.resolve(reference)

    def test_attachment_path_escape_rejected(self):
        for title in ('../test.gif', 'C:\\test.gif', 'sub/test.gif', 'test.gif:stream', 'CON.gif'):
            reference = self.make_gif_attachment(title=title)
            with self.subTest(title=title), self.assertRaises(VisionError):
                self.resolver.resolve(reference)

    def test_resource_exact_lookup_and_protobuf_identifier(self):
        resource_id = 'c' * 32
        resource = self.message_dir / 'message_resource.db'
        with database(resource) as connection:
            connection.execute('CREATE TABLE MessageResourceInfo(message_svr_id INTEGER,message_local_id INTEGER,message_create_time INTEGER,packed_info BLOB)')
            connection.execute('INSERT INTO MessageResourceInfo VALUES(101,7,1790000000,?)', (b'\x0a\x10' + bytes.fromhex(resource_id),))
            connection.execute('INSERT INTO MessageResourceInfo VALUES(102,7,1790000000,?)', (('d' * 32).encode(),))
        self.put_image(digest=resource_id)
        self.assertEqual(self.resolver.resolve(self.reference).width, 24)
        with database(resource) as connection:
            connection.execute('UPDATE MessageResourceInfo SET message_svr_id=999')
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    def test_blob_parsing_is_bounded_and_never_sliding_window(self):
        self.assertEqual(_blob_ids(b'\x0a\x10' + bytes.fromhex('e' * 32)), ['e' * 32])
        self.assertEqual(_blob_ids(b'z' * 65537), [])
        self.assertEqual(_blob_ids(b'z' * 48), [])
        many = b' '.join(f'{index:032x}'.encode() for index in range(400))
        self.assertLessEqual(len(_blob_ids(many)), 256)

    def test_resource_rows_and_blob_sizes_are_limited(self):
        resource_id = 'c' * 32
        self.put_image(digest=resource_id)
        resource = self.message_dir / 'message_resource.db'
        with database(resource) as connection:
            connection.execute('CREATE TABLE MessageResourceInfo(message_svr_id INTEGER,message_local_id INTEGER,message_create_time INTEGER,packed_info BLOB)')
            for index in range(8):
                connection.execute('INSERT INTO MessageResourceInfo VALUES(101,7,1790000000,?)', (f'{index:032x}'.encode(),))
            connection.execute('INSERT INTO MessageResourceInfo VALUES(101,7,1790000000,?)', (resource_id.encode(),))
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)
        with database(resource) as connection:
            connection.execute('DELETE FROM MessageResourceInfo')
            connection.execute('INSERT INTO MessageResourceInfo VALUES(101,7,1790000000,?)', (resource_id.encode() + b'z' * 65536,))
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    def test_symlink_parent_rejected(self):
        file = self.put_image()
        original = file.parent
        outside = self.account / 'other_images'
        original.rename(outside)
        try:
            original.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        with self.assertRaises(VisionError):
            self.resolver.resolve(self.reference)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction behavior')
    def test_junction_parent_inside_account_rejected(self):
        file = self.put_image()
        original = file.parent
        outside = self.account / 'other_images'
        original.rename(outside)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(original), str(outside)],
                                capture_output=True, check=False)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        try:
            with self.assertRaises(VisionError):
                self.resolver.resolve(self.reference)
        finally:
            # Remove only the directory junction itself; never recurse into it.
            os.rmdir(original)


if __name__ == '__main__':
    unittest.main()
