import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_add_update_and_delete(self):
        first = self.store.add('qq', 'private:1', 'preference', '用户不喜欢香菜',
                               key='food.cilantro', confidence=0.9, importance=0.7)
        second = self.store.add('qq', 'private:1', 'preference', '用户很讨厌香菜',
                                key='food.cilantro', confidence=0.95, importance=0.8)
        self.assertEqual(first, second)
        items = self.store.list('qq', 'private:1')
        self.assertEqual(len(items), 1)
        self.assertIn('讨厌', items[0].content)
        self.assertEqual(self.store.delete('qq', 'private:1', '香菜'), 1)
        self.assertEqual(self.store.list('qq', 'private:1'), [])

    def test_users_and_platforms_are_isolated(self):
        self.store.add('qq', 'private:1', 'preference', 'QQ 用户喜欢鲸鱼')
        self.store.add('wechat', 'private:1', 'preference', '微信用户喜欢猫')
        qq = self.store.format_for_prompt('qq', 'private:1', '鲸鱼')
        wx = self.store.format_for_prompt('wechat', 'private:1', '猫')
        self.assertIn('鲸鱼', qq)
        self.assertNotIn('猫', qq)
        self.assertIn('猫', wx)
        self.assertNotIn('鲸鱼', wx)

    def test_summary_and_prompt(self):
        self.store.add('wechat', 'private:2', 'event', '用户下周要考试', importance=0.8)
        self.store.save_summary('wechat', 'private:2', '用户最近在准备考试。', 30)
        block = self.store.format_for_prompt('wechat', 'private:2', '考试')
        self.assertIn('用户最近在准备考试', block)
        self.assertIn('下周要考试', block)

    def test_empty_prompt_is_empty(self):
        self.assertEqual(self.store.format_for_prompt('qq', 'private:none'), '')


if __name__ == '__main__':
    unittest.main()
