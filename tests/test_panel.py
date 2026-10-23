import logging
import tempfile
import unittest
from pathlib import Path

from chat_bridge.health import collect_health
from chat_bridge.logging_setup import configure_logging, tail_log
from chat_bridge.memory import MemoryStore
from chat_bridge.settings import Settings


def settings(root: Path) -> Settings:
    return Settings(
        state_db=root / 'state.sqlite3',
        model_name='fixture',
        model_base_url='https://fixture.example',
        allowed_model_hosts=frozenset({'fixture.example'}),
        model_api_key='test',
        memory_db=root / 'memory.sqlite3',
        log_dir=root / 'logs',
    )


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = settings(self.root)

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, 'baseFilename', '').startswith(str(self.root)):
                root.removeHandler(handler)
                handler.close()
        self.temp.cleanup()

    def test_health_reports_memory_count(self):
        store = MemoryStore(self.settings.memory_db)
        store.add('qq', 'private:1', 'preference', '用户喜欢冰美式')
        health = collect_health(self.settings)
        self.assertEqual(health['memory']['count'], 1)
        self.assertIn('qq_webhook', health['ports'])

    def test_list_all_search_and_delete(self):
        store = MemoryStore(self.settings.memory_db)
        first = store.add('qq', 'private:1', 'preference', '用户喜欢冰美式')
        store.add('wechat', 'private:2', 'event', '用户下周考试')
        self.assertEqual(len(store.list_all()), 2)
        self.assertEqual(len(store.list_all('冰美式')), 1)
        self.assertTrue(store.delete_by_id(first))
        self.assertEqual(len(store.list_all()), 1)

    def test_rotating_log_tail(self):
        configure_logging(self.settings.log_dir)
        logging.getLogger('fixture').info('panel test line')
        self.assertIn('panel test line', '\n'.join(tail_log(self.settings.log_dir, 20)))


if __name__ == '__main__':
    unittest.main()
