import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_ai_bridge import cli
from wechat_ai_bridge.settings import Settings
from wechat_ai_bridge.state.store import StateStore


class MemeCliTests(unittest.TestCase):
    def settings(self, root, **extra):
        return Settings(state_db=root/'state.db', model_name='fixture', model_base_url='https://fixture.test',
            allowed_model_hosts=frozenset({'fixture.test'}), model_api_key='', meme_cache_dir=root/'memes', **extra)

    def test_memes_sync_does_not_load_database_or_model_or_native_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            catalog = Mock()
            catalog.sync.return_value = {'count': 19, 'source': 'official-fixture'}
            with patch('sys.argv', ['bridge', 'memes', 'sync']), \
                 patch.object(cli.Settings, 'from_env', return_value=settings), \
                 patch.object(cli, 'create_meme_catalog', return_value=catalog), \
                 patch.object(cli, 'StateStore') as store, patch.object(cli, 'create_model') as model, \
                 patch.object(cli, 'NativeClient') as native, patch('sys.stdout', new_callable=io.StringIO) as output:
                cli.main()
            self.assertIn('19', output.getvalue())
            store.assert_not_called()
            model.assert_not_called()
            native.assert_not_called()
            catalog.sync.assert_called_once()

    def test_disabled_feature_does_not_create_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory), memes_enabled=False)
            self.assertIsNone(cli.create_meme_catalog(settings))

    def test_explicit_none_disables_memes_and_default_keeps_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            store = StateStore(settings.state_db)
            try:
                self.assertIsNone(cli.create_processor(settings, store, meme_catalog=None).chat.meme_catalog)
                self.assertIsNotNone(cli.create_processor(settings, store).chat.meme_catalog)
            finally:
                store.close()

    def test_frequency_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            for values in [{'meme_cooldown_seconds': -1}, {'meme_recent_count': 21}, {'memes_enabled': 'true'}]:
                with self.subTest(values=values), self.assertRaises(ValueError):
                    self.settings(Path(directory), **values)


if __name__ == '__main__':
    unittest.main()
