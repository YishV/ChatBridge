import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryService, SendReceipt


class CaptureModel:
    def __init__(self):
        self.calls = []
        self.error = None

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return '记住了。'


class Sender:
    def __init__(self, confirmed=True):
        self.calls = 0
        self.confirmed = confirmed

    def send_group_text(self, group, text):
        self.calls += 1
        return SendReceipt(self.confirmed, 'fixture-server' if self.confirmed else None)


class PersistentChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.store = StateStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.store.allow_group('group-a')
        self.model = CaptureModel()
        self.sender = Sender()
        self.service = self.build(self.store, self.model, self.sender)

    def build(self, store, model, sender):
        chat = ChatService(model, alias_key=b'p'*32, history_store=store,
                           history_groups=['group-a'], history_limit=50)
        processor = MessageProcessor(store, chat, cooldown_seconds=0, retry_sleep=lambda _: None)
        return DeliveryService(processor, store, sender)

    def message(self, event='one', sender='member-a', text='我喜欢跑步。', mentioned=True):
        return IncomingMessage(event, 'group-a', sender, text, mentioned, sender_name='小青')

    def test_restart_restores_user_and_confirmed_reply_without_replaying(self):
        self.service.handle(self.message())
        self.store.connection.execute("UPDATE chat_history SET created_at='2000-01-01T00:00:00+00:00'")
        self.store.connection.commit()
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        model, sender = CaptureModel(), Sender()
        with patch('wechat_ai_bridge.qa.service.monotonic', return_value=100000):
            service = self.build(reopened, model, sender)
            self.assertEqual(model.calls, [])
            self.assertEqual(sender.calls, 0)
            result = service.handle(self.message('two', text='我喜欢什么？'))
        self.assertEqual(result.processed.outcome, Outcome.ANSWERED)
        request = model.calls[-1]
        self.assertIn('我喜欢跑步。', str(request))
        self.assertIn('小青', str(request))
        self.assertIn('记住了。', str(request))
        self.assertEqual(request[1]['name'], request[-1]['name'])
        self.assertIn('assistant_reply_targets', request[0]['content'])
        self.assertEqual(sender.calls, 1)
        self.assertEqual(service.handle(self.message()).processed.outcome, Outcome.DUPLICATE)

    def test_complete_text_is_archived_but_model_input_is_bounded(self):
        text = '长正文' * 600
        self.service.handle(self.message(text=text))
        history = self.store.load_chat_history('group-a')
        self.assertEqual(history[0]['content'], text)
        users = [x['content'] for x in self.model.calls[-1] if x['role'] == 'user']
        self.assertLess(len(users[0]), 1200)

    def test_unknown_send_and_model_failure_never_archive_assistant(self):
        self.sender.confirmed = False
        self.service.handle(self.message())
        self.model.error = ModelError(code='model_http_401')
        self.service.handle(self.message('two'))
        rows = self.store.load_chat_history('group-a')
        self.assertEqual([r['role'] for r in rows], ['user', 'user'])

    def test_paused_and_revoked_groups_are_not_archived_or_restored(self):
        self.store.set_paused(True)
        self.service.handle(self.message())
        self.assertEqual(self.store.load_chat_history('group-a'), [])
        self.store.set_paused(False)
        self.service.handle(self.message('two'))
        self.store.deny_group('group-a')
        self.assertEqual(self.store.load_chat_history('group-a'), [])
        model = CaptureModel()
        service = self.build(self.store, model, Sender())
        self.assertEqual(list(service.processor.chat._recent('group-a')), [])

    def test_latest_fifty_are_restored_without_new_model_calls(self):
        processor = MessageProcessor(self.store, self.service.processor.chat,
                                     participation_mode='mentions', cooldown_seconds=0)
        for i in range(60):
            processor.process(self.message(str(i), text=f'离线消息{i}', mentioned=False))
        model = CaptureModel()
        service = self.build(self.store, model, Sender())
        self.assertEqual(model.calls, [])
        history = service.processor.chat._recent('group-a')
        self.assertEqual(len(history), 50)
        self.assertTrue(history[0].content.endswith('离线消息10'))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM chat_history').fetchone()[0], 60)


if __name__ == '__main__':
    unittest.main()
