import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryService, SendReceipt


class SequenceModel:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0
        self.before_call = lambda: None

    def complete(self, messages):
        self.calls += 1
        self.before_call()
        result = next(self.values)
        if isinstance(result, Exception):
            raise result
        return result


class ModelRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(Path(self.directory.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.store.allow_group('fixture-group')
        self.message = IncomingMessage('fixture-event', 'fixture-group', 'fixture-member', '你好', True)

    def processor(self, model, quota=60):
        return MessageProcessor(self.store, ChatService(model), cooldown_seconds=0,
                                hourly_quota=quota, retry_sleep=lambda _: None)

    def test_transient_response_error_retries_once_counts_quota_and_sends_only_once(self):
        model = SequenceModel(ModelError(code='model_response_invalid'), '恢复后的回复')
        processor = self.processor(model)
        class Sender:
            calls = 0
            def send_group_text(self, group, text):
                self.calls += 1
                return SendReceipt(True, 'fixture-server-id')
        sender = Sender()
        service = DeliveryService(processor, self.store, sender)
        result = service.handle(self.message)
        self.assertEqual(result.processed.outcome, Outcome.ANSWERED)
        self.assertEqual(result.processed.model_attempts, 2)
        self.assertEqual(model.calls, 2)
        self.assertEqual(sender.calls, 1)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)
        self.assertEqual(service.handle(self.message).processed.outcome, Outcome.DUPLICATE)
        self.assertEqual(model.calls, 2)
        actions = self.store.connection.execute("SELECT action FROM audit WHERE action LIKE 'model_failure:%'").fetchall()
        self.assertEqual(actions, [('model_failure:model_response_invalid:attempt1',)])

    def test_repeated_transient_failure_is_bounded_and_future_new_message_still_works(self):
        model = SequenceModel(ModelError(code='model_timeout'), ModelError(code='model_timeout'), '下一句正常')
        processor = self.processor(model)
        self.assertEqual(processor.process(self.message).outcome, Outcome.ERROR)
        self.assertEqual(model.calls, 2)
        next_message = IncomingMessage('fixture-next', 'fixture-group', 'fixture-member', '再说一句', True)
        self.assertEqual(processor.process(next_message).outcome, Outcome.ANSWERED)

    def test_authentication_failure_does_not_retry(self):
        model = SequenceModel(ModelError(code='model_http_401'))
        result = self.processor(model).process(self.message)
        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertEqual(model.calls, 1)

    def test_retry_cannot_exceed_hourly_quota(self):
        model = SequenceModel(ModelError(code='model_timeout'), '不能调用这次')
        result = self.processor(model, quota=1).process(self.message)
        self.assertEqual(result.outcome, Outcome.ERROR)
        self.assertEqual(model.calls, 1)

    def test_pause_during_failed_call_prevents_retry(self):
        model = SequenceModel(ModelError(code='model_timeout'), '不能调用这次')
        model.before_call = lambda: self.store.set_paused(True)
        result = self.processor(model).process(self.message)
        self.assertEqual(result.outcome, Outcome.IGNORED)
        self.assertEqual(model.calls, 1)


if __name__ == '__main__':
    unittest.main()
