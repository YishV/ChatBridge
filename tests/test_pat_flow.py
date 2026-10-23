import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from chat_bridge.dispatch.processor import MessageProcessor
from chat_bridge.message import IncomingMessage, Outcome
from chat_bridge.qa.service import ChatService
from chat_bridge.state.store import StateStore
from chat_bridge.wechat.delivery import DeliveryOutcome, DeliveryService, SendReceipt
from chat_bridge.wechat.events import GatewayMessage, normalize_gateway_message


class Model:
    def __init__(self):
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return "拍到我啦，想聊什么？"

    def complete_json(self, messages):
        raise AssertionError("pat must use the directed text branch")


class Sender:
    def __init__(self):
        self.calls = []

    def send_group_text(self, group, text):
        self.calls.append((group, text))
        return SendReceipt(True, "1002")


class PatFlowTests(unittest.TestCase):
    def raw(self, **changes):
        fields = dict(message_id="pat-1", conversation_id="fixture@chatroom", sender_id="member",
            text="[拍一拍] 群友拍了拍你。", message_type="pat", is_group=True,
            is_from_bot=False, mentioned_account_ids=None, target_account_id="bot")
        fields.update(changes)
        return GatewayMessage(**fields)

    def test_pat_is_a_separate_directed_interaction_not_a_fake_mention(self):
        event = normalize_gateway_message(self.raw(), "bot")
        self.assertFalse(event.mentioned_bot)
        self.assertEqual(event.interaction, "pat")
        self.assertTrue(event.is_directed)
        for changes in ({"target_account_id": None}, {"target_account_id": "other"},
                        {"is_group": False}, {"sender_id": "bot"}, {"is_from_bot": True}):
            with self.subTest(changes=changes):
                self.assertIsNone(normalize_gateway_message(self.raw(**changes), "bot"))

    def test_text_that_says_pat_does_not_get_directed_priority(self):
        event = normalize_gateway_message(self.raw(message_type="text"), "bot")
        self.assertEqual(event.interaction, "text")
        self.assertFalse(event.is_directed)

    def test_pat_uses_persona_direct_text_and_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db")
            try:
                store.allow_group("fixture@chatroom")
                model, sender = Model(), Sender()
                processor = MessageProcessor(store, ChatService(model, "你叫测试伙伴，回复简短。"), participation_mode="mentions")
                delivery = DeliveryService(processor, store, sender)
                event = normalize_gateway_message(self.raw(), "bot")
                result = delivery.handle(event)
                self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
                self.assertEqual(delivery.handle(event).processed.outcome, Outcome.DUPLICATE)
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(len(sender.calls), 1)
                self.assertIn("你叫测试伙伴", model.calls[0][0]["content"])
                self.assertIn("拍一拍", model.calls[0][0]["content"])
                self.assertEqual(delivery.handle(replace(event, message_id="pat-2")).processed.outcome, Outcome.RATE_LIMITED)
            finally:
                store.close()

    def test_pat_cancels_pending_natural_message_and_respects_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.db")
            try:
                store.allow_group("fixture@chatroom")
                now = [0]
                model = Model()
                processor = MessageProcessor(store, ChatService(model), cooldown_seconds=0, clock=lambda: now[0])
                event = normalize_gateway_message(self.raw(), "bot")
                self.assertEqual(processor.process(IncomingMessage("text-1", event.group_id, "member", "普通消息", False)).outcome, Outcome.DEFERRED)
                self.assertEqual(processor.process(event).outcome, Outcome.ANSWERED)
                now[0] = 6
                self.assertEqual(processor.flush_pending(), [])
                store.set_paused(True)
                self.assertEqual(processor.process(replace(event, message_id="pat-2")).outcome, Outcome.IGNORED)
                self.assertEqual(len(model.calls), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
