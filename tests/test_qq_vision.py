import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qq.vision import QQVisionInbox
from wechat_ai_bridge.state.store import StateStore


def png_bytes():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(output, 'PNG')
    return output.getvalue()


class QQVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.store.allow_group('1')
        self.now = 100.0
        self.inbox = QQVisionInbox(frozenset({'1'}), self.store, window_seconds=120,
                                   clock=lambda: self.now, fetch=lambda url: png_bytes())

    def test_recent_image_can_be_read_after_explicit_mention(self):
        observed = self.inbox.observe_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1, 'user_id': 2,
            'message': [{'type': 'image', 'data': {'url': 'https://example.test/a.png'}}],
        }, 999)
        self.assertTrue(observed)
        prepared = self.inbox.prepare(
            IncomingMessage('m1', '1', '2', '看图', True), guard=lambda: None)
        self.assertIsNotNone(prepared)
        self.assertIsNotNone(prepared.image)
        self.assertEqual(prepared.image.width, 8)

    def test_image_from_other_member_is_not_used(self):
        self.inbox.observe_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1, 'user_id': 3,
            'message': [{'type': 'image', 'data': {'url': 'https://example.test/a.png'}}],
        }, 999)
        prepared = self.inbox.prepare(
            IncomingMessage('m2', '1', '2', '看图', True), guard=lambda: None)
        self.assertIsNotNone(prepared)
        self.assertIsNone(prepared.image)
        self.assertIn('请先', prepared.reply)

    def test_plain_mention_does_not_upload_an_old_image(self):
        self.inbox.observe_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1, 'user_id': 2,
            'message': [{'type': 'image', 'data': {'url': 'https://example.test/a.png'}}],
        }, 999)
        self.assertIsNone(self.inbox.prepare(
            IncomingMessage('m3', '1', '2', '今天吃什么', True), guard=lambda: None))


if __name__ == '__main__':
    unittest.main()
