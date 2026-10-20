import unittest
from types import SimpleNamespace

from wechat_ai_bridge.message import ChatReply, IncomingMessage
from wechat_ai_bridge.qa.service import ChatService


class DecisionModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_decision(self, messages, *, meme_ids=()):
        self.calls.append((messages, tuple(meme_ids)))
        return self.response

    def complete(self, messages):
        raise AssertionError("plain completion must not be used for this decision")

    def complete_json(self, messages):
        raise AssertionError("JSON mode must not be used when a decision tool is available")


class Catalog:
    assets = (SimpleNamespace(id="083", description="开心偷笑"),)

    def candidates(self, text, *, excluded=(), limit=12):
        return [asset for asset in self.assets if asset.id not in excluded][:limit]

    def get(self, asset_id):
        return next((asset for asset in self.assets if asset.id == asset_id), None)


class DecisionChatTests(unittest.TestCase):
    def test_natural_silence_is_a_decision_value_not_an_empty_response_error(self):
        model = DecisionModel('{"reply":""}')
        chat = ChatService(model)
        reply = chat.generate(IncomingMessage("m1", "group-a", "member", "哈哈", False))
        self.assertEqual(reply, ChatReply())
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][1], ())

    def test_natural_reply_uses_decision_tool(self):
        model = DecisionModel('{"reply":"好啊"}')
        chat = ChatService(model)
        reply = chat.generate(IncomingMessage("m1", "group-a", "member", "周末去哪", False))
        self.assertEqual(reply.reply, "好啊")
        self.assertIsNone(reply.meme_id)
        self.assertEqual(model.calls[0][1], ())

    def test_directed_reply_passes_candidate_ids_and_keeps_meme(self):
        model = DecisionModel('{"reply":"笑死","meme_id":"083"}')
        chat = ChatService(model, meme_catalog=Catalog())
        reply = chat.generate(IncomingMessage("m1", "group-a", "member", "太好笑了", True))
        self.assertEqual(reply.reply, "笑死")
        self.assertEqual(reply.meme_id, "083")
        self.assertEqual(model.calls[0][1], ("083",))
        system = model.calls[0][0][0]["content"]
        self.assertIn("decide_reply", system)
        self.assertIn("083", system)


if __name__ == "__main__":
    unittest.main()
