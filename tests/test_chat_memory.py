import tempfile
import time
import unittest
from pathlib import Path

from chat_bridge.memory import MemoryStore
from chat_bridge.message import IncomingMessage
from chat_bridge.qa.service import ChatService


class Model:
    def __init__(self, extraction=None):
        self.extraction = extraction
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return '好呀。'

    def complete_json(self, messages):
        self.calls.append(messages)
        return self.extraction or '{"action":"none"}'


class ChatMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.model = Model()
        self.chat = ChatService(self.model, '你是测试助手。', memory=self.store, platform='qq')

    def tearDown(self):
        self.temp.cleanup()

    def message(self, text, *, group=False):
        return IncomingMessage('m-' + text, 'group-1' if group else 'private:1',
                               'user-1', text, True, is_group=group)

    def test_private_remember_and_view(self):
        reply = self.chat.generate(self.message('记住：我不吃香菜'))
        self.assertIn('记住', reply.reply)
        self.assertEqual(self.model.calls, [])
        self.assertIn('不吃香菜', self.chat.generate(self.message('查看记忆')).reply)
        self.assertIn('不吃香菜', self.store.format_for_prompt('qq', 'private:1'))

    def test_save_memory_synonym(self):
        reply = self.chat.generate(self.message('保存记忆：我喜欢鲸鱼'))
        self.assertIn('记住', reply.reply)
        self.assertIn('鲸鱼', self.store.format_for_prompt('qq', 'private:1'))

    def test_private_forget(self):
        self.store.add('qq', 'private:1', 'preference', '用户不喜欢香菜')
        reply = self.chat.generate(self.message('忘记香菜'))
        self.assertIn('忘掉', reply.reply)
        self.assertEqual(self.store.list('qq', 'private:1'), [])

    def test_private_memory_is_injected_into_prompt(self):
        self.store.add('qq', 'private:1', 'preference', '用户不喜欢香菜')
        self.chat.generate(self.message('晚上吃什么'))
        system = self.model.calls[0][0]['content']
        self.assertIn('用户不喜欢香菜', system)
        self.assertIn('长期记忆', system)

    def test_group_does_not_get_private_memory(self):
        self.store.add('qq', 'private:1', 'preference', '用户不喜欢香菜')
        self.chat.generate(self.message('晚上吃什么', group=True))
        system = self.model.calls[0][0]['content']
        self.assertNotIn('用户不喜欢香菜', system)

    def test_auto_extraction_runs_after_reply(self):
        model = Model('{"action":"upsert","kind":"preference","key":"drink",'
                      '"content":"用户喜欢冰美式","confidence":0.95,"importance":0.7}')
        chat = ChatService(model, '你是测试助手。', memory=self.store, platform='qq', memory_update_every=1)
        message = self.message('我喜欢冰美式')
        chat.generate(message)
        chat.record_reply(message, '好呀。')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.store.list('qq', 'private:1'):
            time.sleep(0.02)
        self.assertTrue(self.store.list('qq', 'private:1'))


if __name__ == '__main__':
    unittest.main()
