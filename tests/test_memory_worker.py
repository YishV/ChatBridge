import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.memory import MemoryStore
from wechat_ai_bridge.memory.worker import MemoryTask, MemoryWorker


class Model:
    def __init__(self):
        self.extractions = []
        self.summaries = 0

    def complete_json(self, messages):
        self.extractions.append(messages)
        return ('{"action":"upsert","kind":"preference","key":"drink",'
                '"content":"用户喜欢冰美式","confidence":0.95,"importance":0.7}')

    def complete(self, messages):
        self.summaries += 1
        return '用户最近在聊饮料偏好。'


class MemoryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.model = Model()
        self.worker = MemoryWorker(
            self.model, self.store, 'qq',
            update_every=3, summary_every=3, queue_size=8,
        )

    def tearDown(self):
        self.temp.cleanup()

    def task(self, number):
        message = IncomingMessage(
            f'm{number}', 'private:1', 'u1', '我喜欢冰美式', True, is_group=False
        )
        return MemoryTask(message, '好。', (('user', '我喜欢冰美式'),), number)

    def test_extracts_every_third_turn_and_summarizes(self):
        for number in (1, 2, 3):
            self.assertTrue(self.worker.submit(self.task(number)))
        self.worker.drain()
        items = self.store.list('qq', 'private:1')
        self.assertEqual(len(items), 1)
        self.assertEqual(self.store.get_summary('qq', 'private:1'), '用户最近在聊饮料偏好。')
        self.assertEqual(len(self.model.extractions), 1)
        self.assertEqual(self.model.summaries, 1)

    def test_queue_accepts_bounded_tasks(self):
        for number in range(3):
            self.assertTrue(self.worker.submit(self.task(number + 1)))
        self.worker.drain()


if __name__ == '__main__':
    unittest.main()
