import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from wechat_ai_bridge.qq.config import QQSettings
from wechat_ai_bridge.qq.events import map_event
from wechat_ai_bridge.qq.gateway import QQGateway
from wechat_ai_bridge.qq.onebot import OneBotClient, OneBotError
from wechat_ai_bridge.qq.sender import QQSender
from wechat_ai_bridge.qq.webhook import QQWebhookServer
from wechat_ai_bridge.speech.gpt_sovits import GPTSoVITSSettings


class OneBotServer:
    def __init__(self):
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(length))
                owner.calls.append((self.headers.get('Authorization'), body))
                action = self.path.rstrip('/').rsplit('/', 1)[-1]
                data = None
                if action in ('send_group_msg', 'get_login_info'):
                    data = {'message_id': 42, 'user_id': 10001, 'nickname': 'bot'}
                elif action == 'get_group_list':
                    data = [{'group_id': 123, 'group_name': '测试群'}]
                elif action == 'get_friend_list':
                    data = [{'user_id': 456, 'nickname': '好友'}]
                payload = {'status': 'ok', 'retcode': 0, 'data': data}
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class QQEventTests(unittest.TestCase):
    def test_group_at_is_directed_and_text_is_clean(self):
        message = map_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 2, 'message_id': 9, 'self_id': 999,
            'sender': {'nickname': '小明'},
            'message': [
                {'type': 'at', 'data': {'qq': '999'}},
                {'type': 'text', 'data': {'text': ' 在吗'}},
            ],
        }, 999)
        self.assertTrue(message.mentioned_bot)
        self.assertEqual(message.text, '在吗')
        self.assertEqual(message.sender_name, '小明')

    def test_other_at_is_not_directed(self):
        message = map_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 2, 'message_id': 9, 'self_id': 999,
            'message': [{'type': 'at', 'data': {'qq': '1000'}},
                        {'type': 'text', 'data': {'text': '你好'}}],
        }, 999)
        self.assertFalse(message.mentioned_bot)

    def test_poke_targeting_self_becomes_pat(self):
        message = map_event({
            'post_type': 'notice', 'notice_type': 'notify', 'sub_type': 'poke',
            'group_id': 1, 'user_id': 2, 'target_id': 999, 'time': 123,
        }, 999)
        self.assertEqual(message.interaction, 'pat')
        self.assertTrue(message.message_id.startswith('poke:'))

    def test_self_message_is_marked(self):
        message = map_event({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 999, 'message_id': 9, 'self_id': 999,
            'message': [{'type': 'text', 'data': {'text': '自己'}}],
        }, 999)
        self.assertTrue(message.is_from_bot)

    def test_private_message_is_a_directed_private_conversation(self):
        message = map_event({
            'post_type': 'message', 'message_type': 'private', 'user_id': 2,
            'message_id': 9, 'self_id': 999,
            'sender': {'nickname': '好友'},
            'message': [{'type': 'text', 'data': {'text': '在吗'}}],
        }, 999)
        self.assertFalse(message.is_group)
        self.assertEqual(message.group_id, 'private:2')
        self.assertTrue(message.mentioned_bot)
        self.assertTrue(message.is_directed)


class QQGatewayTests(unittest.TestCase):
    def test_tick_maps_queued_group_event(self):
        class Service:
            def __init__(self):
                self.seen = []
                self.flushes = 0
            def handle(self, message):
                self.seen.append(message)
                return type('Result', (), {'outcome': 'answered'})()
            def flush_pending(self):
                self.flushes += 1
                return []

        service = Service()
        gateway = QQGateway(QQSettings(allowed_groups=frozenset({'1'})),
                            client=None, service=service, self_id=999)
        gateway._enqueue({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 2, 'message_id': 9, 'self_id': 999,
            'message': [{'type': 'text', 'data': {'text': '你好'}}],
        })
        counts = gateway.tick()
        self.assertEqual(counts['received'], 1)
        self.assertEqual(service.seen[0].text, '你好')
        self.assertEqual(service.flushes, 1)


class QQWebhookTests(unittest.TestCase):
    def test_signed_webhook_queues_event(self):
        received = []
        settings = QQSettings(webhook_port=0, webhook_secret='s3cret',
                              allowed_groups=frozenset({'1'}))
        server = QQWebhookServer(settings, received.append)
        try:
            port = server.start()
            body = json.dumps({'post_type': 'message', 'x': 1}).encode()
            signature = hmac.new(b's3cret', body, hashlib.sha1).hexdigest()
            request = urllib.request.Request(
                f'http://127.0.0.1:{port}/onebot', data=body,
                headers={'Content-Type': 'application/json', 'X-Signature': signature},
                method='POST')
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 204)
        finally:
            server.stop()
        self.assertEqual(received, [{'post_type': 'message', 'x': 1}])

    def test_bad_signature_is_rejected(self):
        settings = QQSettings(webhook_port=0, webhook_secret='s3cret')
        server = QQWebhookServer(settings, lambda event: None)
        try:
            port = server.start()
            request = urllib.request.Request(
                f'http://127.0.0.1:{port}/onebot', data=b'{}',
                headers={'Content-Type': 'application/json', 'X-Signature': 'bad'}, method='POST')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, 401)
        finally:
            server.stop()


class OneBotClientTests(unittest.TestCase):
    def test_sends_text_and_record_segments(self):
        with TemporaryDirectory() as temp, OneBotServer() as server:
            qq = QQSettings(api_url=server.url, access_token='token')
            client = OneBotClient(qq)
            self.assertEqual(client.send_group_text('123', '你好'), '42')
            wav = Path(temp) / 'voice.wav'
            wav.write_bytes(b'RIFF')
            self.assertEqual(client.send_group_record('123', wav), '42')
            image = Path(temp) / 'image.gif'
            image.write_bytes(b'GIF89a')
            self.assertEqual(client.send_group_image('123', image), '42')
            self.assertEqual(server.calls[0][0], 'Bearer token')
            text_call = server.calls[0][1]
            self.assertEqual(text_call['message'][0]['type'], 'text')
            record = server.calls[1][1]['message'][0]
            self.assertEqual(record['type'], 'record')
            self.assertTrue(record['data']['file'].startswith('file://'))
            image_segment = server.calls[2][1]['message'][0]
            self.assertEqual(image_segment['type'], 'image')
            self.assertTrue(image_segment['data']['file'].startswith('file://'))

    def test_action_error_is_not_a_receipt(self):
        class FailClient(OneBotClient):
            def call(self, action, params):
                raise OneBotError('onebot_action_failed')
        with TemporaryDirectory() as temp:
            client = FailClient(QQSettings())
            with self.assertRaises(OneBotError):
                client.send_group_text('123', 'hello')

    def test_group_list_is_bounded(self):
        with OneBotServer() as server:
            client = OneBotClient(QQSettings(api_url=server.url))
            self.assertEqual(client.group_list(), [{'group_id': '123', 'group_name': '测试群'}])
            self.assertEqual(client.friend_list(), [{'user_id': '456', 'nickname': '好友'}])


class FakeSpeech:
    settings = GPTSoVITSSettings(base_url='http://127.0.0.1:9880',
                                 ref_audio_path=Path('ref.wav'), max_chars=120)

    def synthesize(self, text, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'RIFF')
        return output


class FakeVoiceClient:
    def __init__(self):
        self.records = []
    def send_group_record(self, group, path):
        self.records.append((group, path))
        return 'voice-1'


class FakePrivateClient:
    def __init__(self):
        self.sent = []
    def send_private_text(self, user, text):
        self.sent.append((user, text))
        return 'private-1'


class FakeImageClient:
    def __init__(self):
        self.images = []
    def send_group_image(self, group, path):
        self.images.append((group, path))
        return 'image-1'


class QQSenderTests(unittest.TestCase):
    def test_group_image_uses_image_segment_receipt(self):
        client = FakeImageClient()
        sender = QQSender(QQSettings(voice_enabled=False), client=client)
        receipt = sender.send_group_image('1', Path('meme.gif'))
        self.assertTrue(receipt.confirmed)
        self.assertEqual(client.images, [('1', Path('meme.gif'))])

    def test_private_text_uses_send_private_msg_target(self):
        client = FakePrivateClient()
        sender = QQSender(QQSettings(private_users=frozenset({'2'}), voice_enabled=False), client=client)
        receipt = sender.send_group_text('private:2', '私聊你好')
        self.assertTrue(receipt.confirmed)
        self.assertEqual(client.sent, [('2', '私聊你好')])

    def test_voice_uses_record_and_returns_receipt(self):
        with TemporaryDirectory() as temp:
            qq = QQSettings(allowed_groups=frozenset({'1'}), voice_enabled=True)
            client = FakeVoiceClient()
            sender = QQSender(qq, client=client, speech=FakeSpeech())
            sender.cache_dir = Path(temp)
            result = sender.send_group_voice('1', '你好')
            self.assertTrue(result.confirmed)
            self.assertEqual(result.server_message_id, 'voice-1')
            self.assertEqual(client.records[0][0], '1')


if __name__ == '__main__':
    unittest.main()
