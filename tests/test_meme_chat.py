import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore


class Model:
    def __init__(self, response):
        self.response, self.calls = response, []

    def complete(self, messages):
        self.calls.append(messages)
        return self.response

    complete_json = complete


class Catalog:
    assets = [SimpleNamespace(id='083', description='开心，掩嘴偷笑'),
              SimpleNamespace(id='123', description='想吃饭，蹭饭')]

    def candidates(self, text, *, excluded=(), limit=12):
        return [a for a in self.assets if a.id not in excluded][:limit]

    def get(self, asset_id):
        return next((a for a in self.assets if a.id == asset_id), None)


class MemeChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.store.allow_group('group-a')
        self.message = IncomingMessage('m1', 'group-a', 'human', '哈哈太好笑了', True)

    def processor(self, response, *, catalog=None):
        self.model = Model(response)
        self.chat = ChatService(self.model, meme_catalog=Catalog() if catalog is None else catalog,
                                history_store=self.store, alias_key=b'm'*32)
        return MessageProcessor(self.store, self.chat, cooldown_seconds=0)

    def test_one_model_call_selects_registered_image_and_text(self):
        result = self.processor('{"reply":"笑死我了","meme_id":"083"}').process(self.message)
        self.assertEqual(result.reply, '笑死我了')
        self.assertEqual(result.meme_id, '083')
        self.assertEqual(len(self.model.calls), 1)
        system = self.model.calls[0][0]['content']
        self.assertIn('083', system)
        self.assertIn('每次最多', system)
        self.assertNotIn('只能进行文字聊天', system)

    def test_invalid_image_id_keeps_valid_text(self):
        for image in ['https://evil.test/x.gif', '../083', '999', [], True]:
            with self.subTest(image=image):
                processor = self.processor(json.dumps({'reply': '照常回复', 'meme_id': image}))
                incoming = IncomingMessage(str(image), 'group-a', 'human', '你好', True)
                result = processor.process(incoming)
                self.assertEqual(result.reply, '照常回复')
                self.assertIsNone(result.meme_id)

    def test_silent_natural_message_cannot_send_an_image(self):
        processor = self.processor('{"reply":null,"meme_id":"083"}')
        self.chat.observe(self.message)
        result = self.chat.generate(IncomingMessage('m2', 'group-a', 'human', '路过', False))
        self.assertIsNone(result.reply)
        self.assertIsNone(result.meme_id)

    def test_image_cooldown_preserves_plain_directed_chat(self):
        processor = self.processor('正常文字')
        self.assertTrue(self.store.begin_media_delivery('previous', 'group-a', '083', cooldown_seconds=60))
        result = processor.process(self.message)
        self.assertEqual(result.reply, '正常文字')
        self.assertIsNone(result.meme_id)
        self.assertNotIn('可选表情素材', self.model.calls[0][0]['content'])

    def test_disabled_catalog_preserves_existing_api(self):
        model = Model('直接回复')
        chat = ChatService(model)
        chat.observe(self.message)
        self.assertEqual(chat.respond(self.message), '直接回复')


if __name__ == '__main__':
    unittest.main()
