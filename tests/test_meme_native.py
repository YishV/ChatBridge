import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.gateway import GatewayConfig, NativeClient, NativeSender
from wechat_ai_bridge.wechat.images import static_gif
from wechat_ai_bridge.wechat.reader import NativeRow
from wechat_ai_bridge.wechat.errors import GatewayError


class MemeNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.outgoing = self.root / 'state/stickers/outgoing'
        self.outgoing.mkdir(parents=True)
        self.path = self.outgoing / 'temp.gif'
        Image.new('RGB', (8, 8), 'blue').save(self.path, 'GIF')
        self.md5 = hashlib.md5(self.path.read_bytes()).hexdigest()
        self.path = self.path.rename(self.outgoing / (self.md5 + '.gif'))
        self.store = StateStore(self.root / 'state.db')
        self.addCleanup(self.store.close)
        self.group = 'fixture@chatroom'
        self.store.allow_group(self.group)

    def row(self, *, md5=None, sender='bot', kind=47, server=123, local=11):
        return NativeRow('message_0.db', local, server, 1000, sender, kind,
                         f'<msg><emoji md5="{md5 or self.md5}"/></msg>', '')

    def test_only_new_same_asset_self_row_with_server_receipt_confirms(self):
        reader, native = Mock(), Mock()
        native.send_image.return_value = {'ret': 0}
        reader.recent.side_effect = [[], [self.row(md5='a'*32), self.row(sender='other'),
                                         self.row(server=0), self.row(local=12)]]
        sender = NativeSender(reader, native, self.store, 'bot', frozenset({self.group}), clock=lambda:1005)
        result = sender.send_group_image(self.group, self.path)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.server_message_id, '123')
        native.send_image.assert_called_once_with(self.group, self.path)

    def test_old_or_accepted_only_is_not_confirmation_and_no_retry(self):
        reader, native = Mock(), Mock()
        reader.recent.return_value = [self.row()]
        native.send_image.return_value = {'ret': 0}
        sender = NativeSender(reader, native, self.store, 'bot', frozenset({self.group}),
                              clock=lambda:1005, receipt_timeout=0)
        self.assertFalse(sender.send_group_image(self.group, self.path).confirmed)
        native.send_image.assert_called_once()

    def test_native_client_uses_only_fixed_outgoing_gif_and_rechecks_policy(self):
        config = GatewayConfig(self.root/'data/runtime/client/Weixin.exe', 'fixture', self.root,
            self.root/'credential', self.root/'token', 'bot', frozenset({self.group}), frozenset(), 'a'*64)
        client = NativeClient(config, 'fixture', can_send=lambda group:True)
        client.health = Mock(return_value={'nativeStickerSend': True})
        client._request = Mock(return_value={'ret':0})
        client.send_image(self.group, self.path)
        route, payload = client._request.call_args.args
        self.assertEqual(route, '/SendStickerMsg')
        self.assertEqual(payload['sticker']['md5'], self.md5)
        client._request.reset_mock()
        wrong = self.root / self.path.name
        wrong.write_bytes(self.path.read_bytes())
        with self.assertRaises(GatewayError):
            client.send_image(self.group, wrong)
        client.can_send = lambda group:False
        with self.assertRaises(GatewayError):
            client.send_image(self.group, self.path)
        client._request.assert_not_called()

    def test_sticker_gif_wider_than_wechat_limit_is_rejected(self):
        for width, accepted in ((1000, True), (1001, False)):
            with self.subTest(width=width):
                path = self.outgoing / f'wide-{width}.gif'
                Image.new('RGB', (width, 8), 'blue').save(path, 'GIF')
                digest = hashlib.md5(path.read_bytes()).hexdigest()
                path = path.rename(self.outgoing / (digest + '.gif'))
                if accepted:
                    self.assertEqual(static_gif(path, directory=self.outgoing)['width'], width)
                else:
                    with self.assertRaises(GatewayError):
                        static_gif(path, directory=self.outgoing)

    def test_unknown_image_send_never_retries(self):
        reader, native = Mock(), Mock()
        reader.recent.return_value = []
        native.send_image.side_effect = TimeoutError()
        sender = NativeSender(reader, native, self.store, 'bot', frozenset({self.group}))
        self.assertFalse(sender.send_group_image(self.group, self.path).confirmed)
        native.send_image.assert_called_once()


if __name__ == '__main__':
    unittest.main()
