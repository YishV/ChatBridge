import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from chat_bridge.media.local_memes import CompositeMemeCatalog, LocalMemeLibrary
from chat_bridge.media.meme_collector import MemeCollector
from chat_bridge.settings import Settings


def jpeg(color=(20, 80, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (96, 96), color).save(buffer, format='JPEG')
    return buffer.getvalue()


class Model:
    def __init__(self, response='{"is_meme":true,"safe":true,"description":"蓝色方块","tags":["蓝色","方块"],"score":0.9}'):
        self.response = response
        self.calls = 0

    def complete_vision(self, messages, *, image):
        self.calls += 1
        return self.response


class Official:
    assets = ()
    def candidates(self, text, *, excluded=(), limit=12):
        return []
    def get(self, asset_id):
        return None
    def prepare(self, asset_id):
        raise ValueError('missing')
    def sync(self):
        return {}


def settings(root: Path) -> Settings:
    return Settings(
        state_db=root / 'state.sqlite3',
        model_name='fixture',
        model_base_url='https://fixture.example',
        allowed_model_hosts=frozenset({'fixture.example'}),
        model_api_key='test',
        meme_library_dir=root / 'library',
        meme_collect_enabled=True,
        meme_collect_min_score=0.75,
        meme_collect_max_per_day=10,
    )


class LocalMemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = LocalMemeLibrary(self.root / 'library')

    def tearDown(self):
        self.temp.cleanup()

    def test_add_search_get_and_dedupe(self):
        data = jpeg()
        item = self.library.add(
            digest='a' * 64, image_data=data, description='蓝色方块',
            tags=('蓝色', '方块'), platform='qq', score=0.9,
        )
        self.assertIsNotNone(item)
        self.assertIsNone(self.library.add(
            digest='a' * 64, image_data=data, description='重复',
            tags=('重复',), platform='qq', score=0.9,
        ))
        self.assertEqual(self.library.get(item.id).description, '蓝色方块')
        self.assertTrue(self.library.prepare(item.id).is_file())
        self.assertEqual(self.library.candidates('蓝色')[0].id, item.id)

    def test_composite_prefers_local(self):
        item = self.library.add(
            digest='b' * 64, image_data=jpeg(), description='蓝色方块',
            tags=('蓝色',), platform='wechat', score=0.9,
        )
        catalog = CompositeMemeCatalog(Official(), self.library, local_first=True)
        self.assertEqual(catalog.candidates('蓝色')[0].id, item.id)
        self.assertEqual(catalog.get(item.id).id, item.id)
        self.assertTrue(catalog.prepare(item.id).is_file())

    def test_collector_serializes_model_and_stores(self):
        model = Model()
        collector = MemeCollector(settings(self.root), self.library, model)
        self.assertTrue(collector.submit_bytes(jpeg(), platform='qq', private=False))
        for _ in range(50):
            if self.library.assets():
                break
            time.sleep(0.05)
        self.assertEqual(model.calls, 1)
        self.assertEqual(len(self.library.assets()), 1)

    def test_unsafe_image_is_not_stored(self):
        model = Model(json.dumps({
            'is_meme': False, 'safe': False, 'description': '截图',
            'tags': ['截图'], 'score': 0.99,
        }))
        collector = MemeCollector(settings(self.root), self.library, model)
        self.assertTrue(collector.submit_bytes(jpeg(), platform='qq', private=False))
        for _ in range(20):
            if model.calls:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertEqual(self.library.assets(), [])


if __name__ == '__main__':
    unittest.main()
