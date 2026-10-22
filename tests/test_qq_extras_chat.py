import json
import unittest

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qa.service import ChatService


class ExtrasModel:
    def __init__(self, decision):
        self.decision = decision
        self.kwargs = None

    def complete_decision(self, messages, **kwargs):
        self.kwargs = kwargs
        return json.dumps(self.decision, ensure_ascii=False)

    def complete(self, messages):
        return self.decision.get('reply', '好。')


class QQExtrasChatTests(unittest.TestCase):
    def message(self):
        return IncomingMessage('m1', 'private:1', '1', '哼，理我一下', True, is_group=False)

    def test_qq_reply_can_choose_face_and_poke(self):
        model = ExtrasModel({'reply': '哼，才不理你。', 'face_id': '13', 'poke': True})
        reply = ChatService(model, '你是测试助手。', platform='qq').generate(self.message())
        self.assertEqual(reply.reply, '哼，才不理你。')
        self.assertEqual(reply.face_id, '13')
        self.assertTrue(reply.poke)
        self.assertIn('face_ids', model.kwargs)
        self.assertTrue(model.kwargs['poke_enabled'])

    def test_unknown_face_is_dropped(self):
        model = ExtrasModel({'reply': '好。', 'face_id': '9999'})
        reply = ChatService(model, '你是测试助手。', platform='qq').generate(self.message())
        self.assertIsNone(reply.face_id)
        self.assertFalse(reply.poke)

    def test_wechat_does_not_offer_qq_extras(self):
        model = ExtrasModel({'reply': '好。', 'face_id': '13', 'poke': True})
        reply = ChatService(model, '你是测试助手。', platform='wechat').generate(self.message())
        self.assertIsNone(reply.face_id)
        self.assertFalse(reply.poke)
        self.assertNotIn('face_ids', model.kwargs or {})


if __name__ == '__main__':
    unittest.main()
