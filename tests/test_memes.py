import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from chat_bridge.media.memes import MemeAsset, MemeCatalog, MemeError


BASE = 'https://raw.githubusercontent.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack/main/'
README = BASE + 'README.md'
ARCHIVE = 'https://aigengtu.com/archive'
CDN = 'https://img.aigengtu.com/meme/'
SAMPLE = ('![吃饭蹭饭](previews/123.webp)\n'
          '![开心点赞认可](previews/010.webp)\n'
          '![捧腹大笑](previews/087.webp)').encode()


class Response(io.BytesIO):
    def __init__(self, value, url):
        super().__init__(value)
        self.url = url

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append((url, timeout, dict(request.header_items())))
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return Response(value, url)


def forbidden(url=ARCHIVE):
    return HTTPError(url, 403, 'Forbidden', {}, None)


class MemeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'memes'
        self.outgoing = self.base / 'outgoing'

    def tearDown(self):
        self.temp.cleanup()

    def catalog(self, values=None):
        self.opener = FakeOpener(values or {ARCHIVE: forbidden(), README: SAMPLE})
        self.build = patch('chat_bridge.media.memes.urllib.request.build_opener', return_value=self.opener)
        self.build_mock = self.build.start()
        self.addCleanup(self.build.stop)
        return MemeCatalog(self.root, self.outgoing)

    def synced(self, images=None):
        values = {ARCHIVE: forbidden(), README: SAMPLE}
        values.update(images or {})
        catalog = self.catalog(values)
        catalog.sync()
        return catalog

    def image(self, *, animated=False, size=(20, 20)):
        from PIL import Image
        output = io.BytesIO()
        frame = Image.new('RGBA', size, (0, 100, 200, 120))
        if animated:
            frame.save(output, format='GIF', save_all=True,
                       append_images=[Image.new('RGB', size, 'red')], duration=100, loop=0)
        else:
            frame.save(output, format='PNG')
        return output.getvalue()

    def test_constructor_is_offline_and_bad_local_catalog_is_empty(self):
        catalog = self.catalog()
        self.assertEqual(catalog.assets, ())
        self.assertEqual(self.opener.calls, [])
        self.root.mkdir()
        (self.root / 'catalog.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(MemeCatalog(self.root, self.outgoing).assets, ())

    def test_sync_official_fallback_is_metadata_only_and_reloads(self):
        catalog = self.synced()
        self.assertEqual(len(catalog.assets), 3)
        self.assertEqual(catalog.get('010').fallback_url, BASE + 'previews/010.webp')
        self.assertEqual(len(self.opener.calls), 2)
        saved = json.loads((self.root / 'catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['source'], README)
        self.assertEqual(len(MemeCatalog(self.root, self.outgoing).assets), 3)
        self.assertFalse(self.outgoing.exists())
        for _, timeout, headers in self.opener.calls:
            self.assertEqual(timeout, 5)
            self.assertNotIn('Authorization', headers)

    def test_primary_archive_extracts_verified_links_and_ignores_other_hosts(self):
        html = ('<a href="/meme/123"><img alt="吃饭蹭饭"></a>'
                '<a href="https://evil.test/meme/010">坏链接</a>'
                '<a href="/meme/321">喝茶休息</a>').encode()
        catalog = self.catalog({ARCHIVE: html})
        status = catalog.sync()
        self.assertEqual(status, {'count': 2, 'source': ARCHIVE})
        self.assertIsNone(catalog.get('010'))
        self.assertIsNone(catalog.get('321').fallback_url)

    def test_candidates_rank_context_and_respect_exclusion_and_limit(self):
        catalog = self.synced()
        self.assertEqual(catalog.candidates('去吃饭吧')[0].id, '123')
        self.assertEqual(catalog.candidates('哈哈大笑')[0].id, '087')
        self.assertNotIn('123', [a.id for a in catalog.candidates('吃饭', excluded=('123',))])
        self.assertEqual(len(catalog.candidates('完全不相关', limit=2)), 2)
        self.assertEqual(catalog.candidates('吃饭', limit=0), [])

    def test_asset_rejects_arbitrary_urls_and_unsafe_ids(self):
        for asset_id, url, fallback in [
            ('../123', CDN + '123.webp', None),
            ('123', 'https://evil.test/123.webp', None),
            ('123', CDN + '010.webp', None),
            ('123', CDN + '123.webp?token=secret', None),
            ('321', CDN + '321.webp', BASE + 'previews/321.webp'),
        ]:
            with self.subTest(asset_id=asset_id, url=url), self.assertRaises(MemeError):
                MemeAsset(asset_id, '素材', url, fallback)

    def test_prepare_converts_static_image_to_md5_gif_and_reuses_verified_cache(self):
        catalog = self.synced({CDN + '123.webp': self.image()})
        path = catalog.prepare('123')
        self.assertEqual(path.parent, self.outgoing.resolve())
        self.assertEqual(path.name, hashlib.md5(path.read_bytes()).hexdigest() + '.gif')
        from PIL import Image
        with Image.open(path) as frame:
            self.assertEqual(frame.format, 'GIF')
            self.assertEqual(frame.n_frames, 1)
        calls = len(self.opener.calls)
        self.assertEqual(MemeCatalog(self.root, self.outgoing).prepare('123'), path)
        self.assertEqual(len(self.opener.calls), calls)
        path.write_bytes(b'bad cache')
        self.assertEqual(catalog.prepare('123'), path)
        self.assertEqual(len(self.opener.calls), calls + 1)

    def test_prepare_downscales_oversized_image_for_sticker_display(self):
        catalog = self.synced({CDN + '123.webp': self.image(size=(1200, 800))})
        path = catalog.prepare('123')
        from PIL import Image
        with Image.open(path) as frame:
            self.assertEqual(frame.format, 'GIF')
            self.assertEqual(frame.n_frames, 1)
            self.assertLessEqual(max(frame.size), 500)
            self.assertGreater(frame.size[0], frame.size[1])

    def test_prepare_uses_official_mirror_when_cdn_forbids(self):
        catalog = self.synced({CDN + '010.webp': forbidden(CDN + '010.webp'),
                               BASE + 'previews/010.webp': self.image()})
        self.assertTrue(catalog.prepare('010').is_file())
        self.assertEqual(self.opener.calls[-1][0], BASE + 'previews/010.webp')

    def test_prepare_rejects_animated_oversize_invalid_and_unknown_assets(self):
        catalog = self.synced()
        for bad in [self.image(animated=True), self.image(size=(4097, 1)), b'not an image', b'x' * (8 * 1024 * 1024 + 1)]:
            self.opener.values[CDN + '123.webp'] = bad
            with self.subTest(size=len(bad)), self.assertRaises(MemeError):
                catalog.prepare('123')
        with self.assertRaisesRegex(MemeError, 'meme_unknown_id'):
            catalog.prepare('../123')

    def test_failed_sync_preserves_previous_metadata_and_safe_error(self):
        catalog = self.synced()
        before = (self.root / 'catalog.json').read_bytes()
        self.opener.values[README] = OSError('private URL or token')
        with self.assertRaises(MemeError) as result:
            catalog.sync()
        self.assertEqual(result.exception.code, 'meme_index_unavailable')
        self.assertNotIn('private', str(result.exception))
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)

    def test_redirects_are_disabled_and_index_size_is_bounded(self):
        catalog = self.catalog({ARCHIVE: b'x' * (2 * 1024 * 1024 + 1), README: SAMPLE})
        catalog.sync()
        handler = self.build_mock.return_value
        self.assertIs(handler, self.opener)
        handlers = self.build_mock.call_args.args
        redirect = next(h for h in handlers if hasattr(h, 'redirect_request'))
        self.assertIsNone(redirect.redirect_request(None, None, 302, 'redirect', {}, 'https://evil.test'))

    def test_response_url_changes_are_rejected_without_contacting_new_host(self):
        catalog = self.synced()
        with patch.object(self.opener, 'open', return_value=Response(self.image(), 'https://evil.test/123.webp')):
            with self.assertRaisesRegex(MemeError, 'meme_redirect_forbidden'):
                catalog.prepare('123')

    def test_forged_catalog_and_cache_paths_never_escape_directories(self):
        catalog = self.synced({CDN + '123.webp': self.image()})
        (self.root / 'cache.json').write_text(json.dumps({'123': '../elsewhere'}), encoding='utf-8')
        path = catalog.prepare('123')
        self.assertEqual(path.parent, self.outgoing.resolve())
        saved = json.loads((self.root / 'catalog.json').read_text(encoding='utf-8'))
        saved['assets'][0]['url'] = 'https://evil.test/123.webp'
        (self.root / 'catalog.json').write_text(json.dumps(saved), encoding='utf-8')
        self.assertEqual(MemeCatalog(self.root, self.outgoing).assets, ())

    def test_cache_must_contain_real_static_gif_even_when_md5_matches(self):
        catalog = self.synced({CDN + '123.webp': self.image()})
        self.outgoing.mkdir()
        png = self.image()
        digest = hashlib.md5(png).hexdigest()
        (self.outgoing / (digest + '.gif')).write_bytes(png)
        (self.root / 'cache.json').write_text(json.dumps({'123': digest}), encoding='utf-8')
        calls = len(self.opener.calls)
        path = catalog.prepare('123')
        self.assertEqual(len(self.opener.calls), calls + 1)
        self.assertNotEqual(path.stem, digest)

    def test_missing_pillow_is_explicit_safe_failure(self):
        catalog = self.synced({CDN + '123.webp': b'image'})
        with patch.dict('sys.modules', {'PIL': None}):
            with self.assertRaisesRegex(MemeError, 'meme_pillow_missing'):
                catalog.prepare('123')


if __name__ == '__main__':
    unittest.main()
