import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, DeliveryService, SendReceipt


class FakeModel:
    def __init__(self, action=None):
        self.calls = []
        self.action = action

    def complete(self, messages):
        self.calls.append(messages)
        if self.action:
            self.action()
        return "周末去散步吧。"


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


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.store.allow_group("group-a")
        self.message = IncomingMessage("m1", "group-a", "user-a", "周末做什么？", True)
        self.model = FakeModel()

    def service(self, sender, model=None):
        self.chat = ChatService(model or self.model)
        processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0)
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

    def test_model_failure_has_no_technical_reply_to_group(self):
        def fail():
            raise ModelError("private diagnostic")
        sender = FakeSender(SendReceipt(True, "server-1"))
        result = self.service(sender, FakeModel(fail)).handle(self.message)
        self.assertEqual(result.processed.outcome, Outcome.ERROR)
        self.assertIsNone(result.processed.reply)
        self.assertEqual(sender.calls, [])


if __name__ == "__main__":
    unittest.main()
