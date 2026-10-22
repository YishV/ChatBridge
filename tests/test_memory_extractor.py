import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.memory import MemoryStore
from wechat_ai_bridge.memory.extractor import apply_memory_action, extract_memory


class Model:
    def __init__(self, response):
        self.response = response

    def complete_json(self, messages):
        return self.response


class MemoryExtractorTests(unittest.TestCase):
    def test_valid_preference(self):
        model = Model('{"action":"upsert","kind":"preference","key":"food.cilantro",'
                      '"content":"用户不喜欢香菜","confidence":0.95,"importance":0.7}')
        action = extract_memory(model, 'qq', 'private:1', '我不吃香菜', '好', [])
        self.assertEqual(action['kind'], 'preference')
        self.assertEqual(action['key'], 'food.cilantro')

    def test_sensitive_or_low_confidence_is_rejected(self):
        for response in (
            '{"action":"none"}',
            '{"action":"upsert","kind":"note","content":"密码是 123456",'
            '"confidence":0.95,"importance":0.5}',
            '{"action":"upsert","kind":"note","content":"普通事实",'
            '"confidence":0.3,"importance":0.5}',
        ):
            with self.subTest(response=response):
                model = Model(response)
                self.assertIsNone(extract_memory(model, 'qq', 'private:1', '哈哈', '好', []))

    def test_apply_upsert_and_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MemoryStore(Path(temp) / 'memory.sqlite3')
            model = Model('{"action":"upsert","kind":"event","key":"exam",'
                          '"content":"用户下周要考试","confidence":0.9,"importance":0.8}')
            apply_memory_action(model, store, 'wechat', 'private:1', '我下周考试', '加油', 'm1')
            self.assertEqual(len(store.list('wechat', 'private:1')), 1)


if __name__ == '__main__':
    unittest.main()
