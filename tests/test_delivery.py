import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome, ProcessResult
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, DeliveryService, SendReceipt
from wechat_ai_bridge.wechat.errors import GatewayFatalError


class FakeModel:
    def __init__(self, action=None, response="周末去散步吧。"):
        self.calls = []
        self.action = action
        self.response = response

    def complete(self, messages):
        self.calls.append(messages)
        if self.action:
            self.action()
        return self.response


class FakeSender:
    def __init__(self, receipt=None, error=None):
        self.calls = []
        self.receipt = receipt
        self.error = error

    def send_group_text(self, group_id, text):
        self.calls.append((group_id, text))
        if self.error:
            raise self.error
        return self.receipt


class ExtraSender(FakeSender):
    def __init__(self):
        super().__init__(SendReceipt(True, 'server-1'))
        self.face_calls = []
        self.poke_calls = []

    def send_face(self, group_id, face_id):
        self.face_calls.append((group_id, face_id))
        return SendReceipt(True, 'face-1')

    def send_poke(self, group_id, user_id):
        self.poke_calls.append((group_id, user_id))
        return SendReceipt(True, 'poke-1')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.store.allow_group("group-a")
        self.message = IncomingMessage("m1", "group-a", "user-a", "周末做什么？", True)
        self.model = FakeModel()

    def service(self, sender, model=None, clock=None):
        self.chat = ChatService(model or self.model)
        options = {} if clock is None else {"clock": clock}
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0, **options)
        return DeliveryService(processor, self.store, sender)

    def next_context(self):
        message = IncomingMessage("m2", "group-a", "user-b", "继续说", True)
        self.chat.observe(message)
        self.chat.respond(message)
        return self.model.calls[-1]

    def test_confirmation_records_assistant_only_after_server_receipt(self):
        sender = FakeSender(SendReceipt(True, "server-1"))
        result = self.service(sender).handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(self.store.delivery_status("m1"), "confirmed")
        self.assertEqual(len(sender.calls), 1)
        assistants = [item for item in self.next_context() if item["role"] == "assistant"]
        self.assertEqual(assistants, [{"role": "assistant", "content": "周末去散步吧。"}])

    def test_timeout_is_unconfirmed_and_duplicate_does_not_resend(self):
        sender = FakeSender(error=TimeoutError("status unknown"))
        service = self.service(sender)
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.UNCONFIRMED)
        self.assertEqual(self.store.delivery_status("m1"), "unconfirmed")
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.NOT_SENT)
        self.assertEqual(len(sender.calls), 1)
        self.assertFalse(any(item["role"] == "assistant" for item in self.next_context()))

    def test_success_without_server_id_does_not_record_assistant(self):
        result = self.service(FakeSender(SendReceipt(True))).handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.UNCONFIRMED)
        self.assertEqual(self.store.delivery_status("m1"), "unconfirmed")
        self.assertFalse(any(item["role"] == "assistant" for item in self.next_context()))

    def test_pause_during_model_call_stops_send(self):
        sender = FakeSender(SendReceipt(True, "server-1"))
        result = self.service(sender, FakeModel(lambda: self.store.set_paused(True))).handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.SKIPPED_PAUSED)
        self.assertEqual(sender.calls, [])
        self.assertIsNone(self.store.delivery_status("m1"))

    def test_group_revoked_during_model_call_stops_send(self):
        sender = FakeSender(SendReceipt(True, "server-1"))
        result = self.service(sender, FakeModel(lambda: self.store.deny_group("group-a"))).handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.SKIPPED_NOT_ALLOWED)
        self.assertEqual(sender.calls, [])
        self.assertIsNone(self.store.delivery_status("m1"))

    def test_qq_face_and_poke_are_sent_after_text(self):
        sender = ExtraSender()
        service = self.service(sender)
        result = service._deliver(
            self.message,
            ProcessResult(Outcome.ANSWERED, '哼。', face_id='13', poke=True),
        )
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(sender.face_calls, [('group-a', '13')])
        self.assertEqual(sender.poke_calls, [('group-a', 'user-a')])

    def test_model_failure_has_no_technical_reply_to_group(self):
        def fail():
            raise ModelError("private diagnostic")
        sender = FakeSender(SendReceipt(True, "server-1"))
        result = self.service(sender, FakeModel(fail)).handle(self.message)
        self.assertEqual(result.processed.outcome, Outcome.ERROR)
        self.assertIsNone(result.processed.reply)
        self.assertEqual(sender.calls, [])

    def test_natural_flush_sends_once_and_records_only_confirmed_reply(self):
        now = [0]
        self.model = FakeModel(response='{"reply":"去散步"}')
        sender = FakeSender(SendReceipt(True, "server-2"))
        service = self.service(sender, clock=lambda: now[0])
        incoming = replace(self.message, mentioned_bot=False)
        self.assertEqual(service.handle(incoming).processed.outcome, Outcome.DEFERRED)
        self.assertEqual(sender.calls, [])
        now[0] = 5
        self.assertEqual(service.flush_pending()[0].delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(service.flush_pending(), [])
        self.assertEqual(service.handle(incoming).processed.outcome, Outcome.DUPLICATE)
        self.assertEqual(sender.calls, [("group-a", "去散步")])
        self.assertEqual(self.store.delivery_status("m1"), "confirmed")
        self.assertEqual([item for item in self.next_context() if item["role"] == "assistant"],
                         [{"role": "assistant", "content": "去散步"}])

    def test_natural_failed_delivery_is_not_retried(self):
        now = [0]
        self.model = FakeModel(response='{"reply":"去散步"}')
        sender = FakeSender(error=TimeoutError("unknown"))
        service = self.service(sender, clock=lambda: now[0])
        incoming = replace(self.message, mentioned_bot=False)
        service.handle(incoming)
        now[0] = 5
        self.assertEqual(service.flush_pending()[0].delivery, DeliveryOutcome.UNCONFIRMED)
        self.assertEqual(service.flush_pending(), [])
        self.assertEqual(service.handle(incoming).processed.outcome, Outcome.DUPLICATE)
        self.assertEqual(len(sender.calls), 1)

    def test_fatal_send_error_is_propagated_and_delivery_consumed(self):
        sender = FakeSender(error=GatewayFatalError("fatal"))
        service = self.service(sender)
        with self.assertRaises(GatewayFatalError):
            service.handle(self.message)
        self.assertEqual(self.store.delivery_status("m1"), "unconfirmed")
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.NOT_SENT)
        self.assertEqual(len(sender.calls), 1)

    def test_natural_flush_rechecks_controls_after_model_call(self):
        for expected in (DeliveryOutcome.SKIPPED_PAUSED, DeliveryOutcome.SKIPPED_NOT_ALLOWED):
            with self.subTest(expected=expected):
                self.store.set_paused(False)
                group_id = str(expected)
                self.store.allow_group(group_id)
                now = [0]
                sender = FakeSender(SendReceipt(True, "server-3"))
                action = (lambda: self.store.set_paused(True)) if expected == DeliveryOutcome.SKIPPED_PAUSED else (
                    lambda: self.store.deny_group(group_id))
                model = FakeModel(action, response='{"reply":"去散步"}')
                service = self.service(sender, model, clock=lambda: now[0])
                incoming = replace(self.message, message_id=str(expected), group_id=group_id, mentioned_bot=False)
                service.handle(incoming)
                now[0] = 5
                self.assertEqual(service.flush_pending()[0].delivery, expected)
                self.assertEqual(sender.calls, [])
                self.assertEqual(service.flush_pending(), [])

    def test_natural_flush_propagates_fatal_send_without_retry(self):
        now = [0]
        sender = FakeSender(error=GatewayFatalError("fatal"))
        service = self.service(sender, FakeModel(response='{"reply":"去散步"}'), clock=lambda: now[0])
        incoming = replace(self.message, mentioned_bot=False)
        service.handle(incoming)
        now[0] = 5
        with self.assertRaises(GatewayFatalError):
            service.flush_pending()
        self.assertEqual(self.store.delivery_status("m1"), "unconfirmed")
        self.assertEqual(service.flush_pending(), [])
        self.assertEqual(len(sender.calls), 1)

    def test_first_group_fatal_stops_second_group_model_call(self):
        now = [0]
        self.store.allow_group("group-b")
        model = FakeModel(response='{"reply":"回答"}')
        sender = FakeSender(error=GatewayFatalError("fatal"))
        service = self.service(sender, model, clock=lambda: now[0])
        service.handle(replace(self.message, mentioned_bot=False))
        service.handle(replace(self.message, message_id="group-b-message", group_id="group-b", mentioned_bot=False))
        now[0] = 5
        with self.assertRaises(GatewayFatalError):
            service.flush_pending()
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(sender.calls), 1)


if __name__ == "__main__":
    unittest.main()
