import io
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

from wechat_ai_bridge.console_log import ConsoleLog
from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, Outcome
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.delivery import DeliveryService, DeliveryOutcome, SendReceipt


class FakeModel:
    def __init__(self, response="周末去散步吧。", action=None):
        self.response = response
        self.action = action
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if self.action:
            self.action()
        return self.response


class FakeSender:
    def __init__(self, receipt=None, error=None):
        self.receipt = receipt
        self.error = error
        self.calls = []

    def send_group_text(self, group_id, text):
        self.calls.append((group_id, text))
        if self.error:
            raise self.error
        return self.receipt


class ConsoleLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.store.allow_group("group-a")
        self.message = IncomingMessage("m1", "group-a", "user-a", "周末做什么？", True)
        self.stream = io.StringIO()
        self.console = ConsoleLog(self.stream, timestamp=lambda: "2026-10-20 11:22:35")

    def service(self, sender, model=None, clock=None):
        processor = MessageProcessor(self.store, ChatService(model or FakeModel()),
                                     cooldown_seconds=0, **({} if clock is None else {"clock": clock}))
        return DeliveryService(processor, self.store, sender, console=self.console)

    def lines(self):
        return self.stream.getvalue().splitlines()

    def test_received_message_and_confirmed_send_are_logged(self):
        sender = FakeSender(SendReceipt(True, "server-1"))
        service = self.service(sender)
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.CONFIRMED)
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("2026-10-20 11:22:35 [收到]"), lines[0])
        self.assertIn("group=group-a", lines[0])
        self.assertIn("sender=user-a", lines[0])
        self.assertIn("kind=@", lines[0])
        self.assertIn("text=周末做什么？", lines[0])
        self.assertTrue(lines[1].startswith("2026-10-20 11:22:35 [发送成功]"), lines[1])
        self.assertIn("serverId=server-1", lines[1])
        self.assertIn("text=周末去散步吧。", lines[1])

    def test_send_without_server_receipt_is_logged_as_unconfirmed(self):
        sender = FakeSender(SendReceipt(True))
        service = self.service(sender)
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.UNCONFIRMED)
        self.assertIn("[发送未确认]", self.lines()[-1])
        self.assertNotIn("[发送成功]", self.stream.getvalue())

    def test_failed_submission_logs_exception_type_without_detail(self):
        sender = FakeSender(error=TimeoutError("secret detail"))
        service = self.service(sender)
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.UNCONFIRMED)
        line = self.lines()[-1]
        self.assertIn("[未发送]", line)
        self.assertIn("reason=发送失败(TimeoutError)", line)
        self.assertNotIn("secret detail", line)

    def test_pause_during_model_call_is_logged(self):
        sender = FakeSender(SendReceipt(True, "server-1"))
        service = self.service(sender, FakeModel(action=lambda: self.store.set_paused(True)))
        result = service.handle(self.message)
        self.assertEqual(result.delivery, DeliveryOutcome.SKIPPED_PAUSED)
        self.assertIn("[未发送]", self.lines()[-1])
        self.assertIn("reason=已暂停", self.lines()[-1])

    def test_deferred_then_silent_model_is_logged_on_flush(self):
        now = [0]
        sender = FakeSender(SendReceipt(True, "server-1"))
        service = self.service(sender, FakeModel(response='{"reply":null}'), clock=lambda: now[0])
        incoming = replace(self.message, mentioned_bot=False, text="今天好热")
        self.assertEqual(service.handle(incoming).processed.outcome, Outcome.DEFERRED)
        self.assertIn("reason=等待合并判断", self.lines()[-1])
        now[0] = 5
        results = service.flush_pending()
        self.assertEqual(len(results), 1)
        self.assertIn("[未回复]", self.lines()[-1])
        self.assertIn("reason=模型选择沉默", self.lines()[-1])

    def test_text_is_kept_on_one_bounded_line(self):
        service = self.service(FakeSender(SendReceipt(True, "server-1")))
        message = replace(self.message, text="第一行\n第二行\x00" + "长" * 600)
        service.handle(message)
        lines = self.lines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("\n", lines[0])
        self.assertNotIn("\x00", lines[0])
        self.assertTrue(lines[0].endswith("…"), lines[0][-20:])

    def test_bot_own_echo_is_not_logged_twice(self):
        service = self.service(FakeSender(SendReceipt(True, "server-1")))
        echo = replace(self.message, sender_id="bot", is_from_bot=True)
        self.assertEqual(service.handle(echo).delivery, DeliveryOutcome.NOT_SENT)
        self.assertEqual(self.lines(), [])

    def test_broken_stream_never_blocks_delivery(self):
        class BrokenStream:
            def write(self, text):
                raise OSError("broken pipe")

            def flush(self):
                raise OSError("broken pipe")

        sender = FakeSender(SendReceipt(True, "server-1"))
        processor = MessageProcessor(self.store, ChatService(FakeModel()), cooldown_seconds=0)
        service = DeliveryService(processor, self.store, sender,
                                  console=ConsoleLog(BrokenStream()))
        self.assertEqual(service.handle(self.message).delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(len(sender.calls), 1)

    def test_default_console_keeps_delivery_silent(self):
        captured = io.StringIO()
        processor = MessageProcessor(self.store, ChatService(FakeModel()), cooldown_seconds=0)
        service = DeliveryService(processor, self.store, FakeSender(SendReceipt(True, "server-1")))
        with redirect_stderr(captured):
            service.handle(self.message)
        self.assertEqual(captured.getvalue(), "")

    def test_default_stream_is_standard_error(self):
        captured = io.StringIO()
        with redirect_stderr(captured):
            ConsoleLog().notice("hello")
        line = captured.getvalue().strip()
        self.assertTrue(line.endswith("[网关] hello"), line)


if __name__ == "__main__":
    unittest.main()
