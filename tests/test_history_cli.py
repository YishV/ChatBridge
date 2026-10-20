import io
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_ai_bridge import cli
from wechat_ai_bridge.qa.identity import load_identity_key
from wechat_ai_bridge.settings import Settings
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.errors import GatewayError


class Model:
    def __init__(self):
        self.calls = []
    def complete(self, messages):
        self.calls.append(messages)
        return '测试回复'


class HistoryCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.settings = Settings(state_db=self.path, model_name='fixture', model_base_url='https://fixture.example',
                                 allowed_model_hosts=frozenset({'fixture.example'}), model_api_key='fixture',
                                 system_prompt='测试人设', cooldown_seconds=0)
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.store.allow_group('group-a')
        self.model = Model()

    def test_separate_cli_invocations_restore_context_automatically(self):
        for event, text in [('one', '我喜欢跑步'), ('two', '我喜欢什么')]:
            arguments = ['wechat-ai-bridge', 'ask', '--group', 'group-a', '--sender', 'fixture-member',
                         '--message-id', event, '--text', text, '--mentioned']
            with patch('sys.argv', arguments), patch.object(cli.Settings, 'from_env', return_value=self.settings), \
                 patch.object(cli, 'create_model', return_value=self.model), patch('sys.stdout', new_callable=io.StringIO):
                cli.main()
        self.assertEqual(len(self.model.calls), 2)
        self.assertIn('我喜欢跑步', str(self.model.calls[-1]))
        self.assertTrue(self.path.with_suffix('.members.key').is_file())

    def test_missing_member_secret_with_existing_history_fails_before_model_creation(self):
        self.store.claim_event('old', 'group-a', chat={'member_id': 'member_012345abcdef',
            'sender_name': '测试', 'content': '旧内容', 'interaction': 'text'})
        with patch.object(cli, 'create_model') as model, self.assertRaisesRegex(GatewayError, 'member_identity_key_missing_for_history'):
            cli.create_processor(self.settings, self.store)
        model.assert_not_called()
        self.assertFalse(self.path.with_suffix('.members.key').exists())

    def test_gateway_check_restores_only_configured_group_without_inference_or_send(self):
        load_identity_key(self.path.with_suffix('.members.key'))
        self.store.allow_group('group-b')
        for group in ['group-a', 'group-b']:
            self.store.claim_event(group, group, chat={'member_id': 'member_012345abcdef',
                'sender_name': '测试', 'content': group, 'interaction': 'text'})
        config = Mock()
        config.groups = frozenset({'group-a'})
        config.reader_credentials.return_value = (Path(self.temp.name), 'fixture')
        native = Mock()
        native.health.return_value = {'ready': True, 'version': 'fixture'}
        with patch.object(cli.GatewayConfig, 'load', return_value=config), \
             patch.object(cli, 'EncryptedMessageReader') as reader, \
             patch.object(cli, 'NativeClient', return_value=native), \
             patch.object(cli, 'create_model', return_value=self.model), patch.object(cli, 'emit') as emit:
            cli.run_gateway(Namespace(check=True), self.settings, self.store)
        self.assertEqual(emit.call_args.args[0]['historyRestored'], 1)
        self.assertEqual(self.model.calls, [])
        native.send_text.assert_not_called()
        reader.return_value.snapshot.assert_called_once_with('group-a')

    def test_history_limit_validation(self):
        for value in (0, 201, True, 1.2, '50'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(state_db=self.path, model_name='fixture', model_base_url='https://fixture.example',
                    allowed_model_hosts=frozenset({'fixture.example'}), model_api_key='', history_context_messages=value)


if __name__ == '__main__':
    unittest.main()
