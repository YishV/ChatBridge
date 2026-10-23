import unittest
from types import SimpleNamespace

from chat_bridge.qq.config import QQSettings
from chat_bridge.qq.gateway import QQGateway


class Singing:
    def __init__(self):
        self.seen = []

    def handle(self, message):
        self.seen.append(message)
        return True


class Service:
    def handle(self, message):
        raise AssertionError('singing should bypass chat')

    def flush_pending(self):
        return []


class QQSingingGatewayTests(unittest.TestCase):
    def test_singing_request_bypasses_chat_service(self):
        singing = Singing()
        gateway = QQGateway(
            QQSettings(allowed_groups=frozenset({'1'})),
            client=SimpleNamespace(), service=Service(), singing=singing,
        )
        gateway.events.put({
            'post_type': 'message', 'message_type': 'group', 'group_id': 1,
            'user_id': 2, 'message_id': 3, 'self_id': 999,
            'message': [{'type': 'text', 'data': {'text': '/随机翻唱'}}],
        })
        result = gateway.tick()
        self.assertEqual(len(singing.seen), 1)
        self.assertEqual(result['received'], 1)


if __name__ == '__main__':
    unittest.main()
