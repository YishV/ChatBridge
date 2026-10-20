import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, DeliveryService, SendReceipt
from wechat_ai_bridge.wechat.errors import GatewayFatalError


class Model:
    def complete(self, messages):
        return '{"reply":"哈哈，太逗了","meme_id":"083"}'
    complete_json = complete


class Catalog:
    def __init__(self, path):
        self.path = path
        self.asset = type('Asset', (), {'id': '083', 'description': '开心偷笑'})()
        self.assets = (self.asset,)
    def candidates(self, *args, **kwargs):
        return [self.asset] if '083' not in kwargs.get('excluded', ()) else []
    def get(self, asset_id):
        return self.asset if asset_id == '083' else None
    def prepare(self, asset_id):
        return self.path


class MemeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / 'state.db')
        self.addCleanup(self.store.close)
        self.store.allow_group('group-a')
        self.image = self.root / 'image.gif'
        Image.new('RGB', (4, 4), 'blue').save(self.image, 'GIF')
        self.catalog = Catalog(self.image)
        self.chat = ChatService(Model(), history_store=self.store, meme_catalog=self.catalog, alias_key=b'm'*32)
        self.sender = Mock()
        self.sender.send_group_text.return_value = SendReceipt(True, 'text-1')
        self.sender.send_group_image.return_value = SendReceipt(True, 'image-1')
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0)
        self.service = DeliveryService(processor, self.store, self.sender, meme_catalog=self.catalog)
        self.message = IncomingMessage('m1', 'group-a', 'human', '好好笑', True)

    def test_confirmed_image_is_separate_history_turn_and_never_resends(self):
        result = self.service.handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(result.media_delivery, DeliveryOutcome.CONFIRMED)
        self.sender.send_group_image.assert_called_once_with('group-a', self.image)
        rows = self.store.load_chat_history('group-a')
        self.assertEqual([r['role'] for r in rows], ['user', 'assistant', 'assistant'])
        self.assertIn('#083', rows[-1]['content'])
        self.service.handle(self.message)
        self.assertEqual(self.sender.send_group_text.call_count, 1)
        self.assertEqual(self.sender.send_group_image.call_count, 1)

    def test_download_failure_preserves_text(self):
        self.catalog.prepare = Mock(side_effect=ValueError('meme_download_failed'))
        result = self.service.handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(result.media_reason, 'meme_prepare_failed')
        self.sender.send_group_image.assert_not_called()
        self.assertEqual(len(self.store.load_chat_history('group-a')), 2)

    def test_unconfirmed_image_has_no_history_and_no_retry(self):
        self.sender.send_group_image.side_effect = TimeoutError()
        result = self.service.handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(result.media_delivery, DeliveryOutcome.UNCONFIRMED)
        self.assertEqual(len(self.store.load_chat_history('group-a')), 2)
        self.service.handle(self.message)
        self.assertEqual(self.sender.send_group_image.call_count, 1)

    def test_unconfirmed_text_does_not_send_image(self):
        self.sender.send_group_text.return_value = SendReceipt(False)
        result = self.service.handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.UNCONFIRMED)
        self.sender.send_group_image.assert_not_called()

    def test_controls_changed_during_prepare_stop_image(self):
        for action in ['pause', 'deny']:
            with self.subTest(action=action):
                self.store.set_paused(False)
                self.store.allow_group('group-a')
                self.catalog.prepare = Mock(side_effect=lambda _: self._change_policy(action))
                incoming = IncomingMessage(action, 'group-a', 'human', '好笑', True)
                result = self.service.handle(incoming)
                self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
                self.sender.send_group_image.assert_not_called()

    def _change_policy(self, action):
        self.store.set_paused(True) if action == 'pause' else self.store.deny_group('group-a')
        return self.image

    def test_fatal_native_change_still_stops_after_preserving_text(self):
        self.sender.send_group_image.side_effect = GatewayFatalError('native_changed')
        with self.assertRaises(GatewayFatalError):
            self.service.handle(self.message)
        self.assertEqual(len(self.store.load_chat_history('group-a')), 2)
        self.assertEqual(self.store.recent_media('group-a'), ['083'])


if __name__ == '__main__':
    unittest.main()
