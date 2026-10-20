import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from wechat_ai_bridge.wechat.gateway import GatewayConfig, GatewayError, GatewayRunner, NativeSender
from wechat_ai_bridge.wechat.reader import EncryptedMessageReader, NativeRow
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.errors import GatewayFatalError
from wechat_ai_bridge.wechat.instance import SingleInstance
from wechat_ai_bridge.wechat.delivery import DeliveryService
from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.message import Outcome, ProcessResult
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, DeliveryResult


def service_mock():
    service = Mock()
    service.flush_pending.return_value = []
    return service


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "state.db")
        self.addCleanup(self.store.close)
        self.group = "fixture@chatroom"
        self.store.allow_group(self.group)

    def row(self, local_id=11, server_id=100, sender="member"):
        return NativeRow("message_0.db", local_id, server_id, 1000, sender, 1,
                         "你好", "<msgsource><atuserlist>bot</atuserlist></msgsource>")

    def test_runner_starts_after_baseline_and_delivers_new_only(self):
        reader = Mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        reader.read_after.return_value = [self.row()]
        service = service_mock()
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        reader.read_after.assert_not_called()
        runner.tick()
        self.assertEqual(service.handle.call_count, 1)
        self.assertEqual(runner.cursors[self.group]["message_0.db"], 11)
        incoming = service.handle.call_args.args[0]
        self.assertEqual(incoming.sender_id, "member")
        self.assertTrue(incoming.mentioned_bot)

    def test_pending_text_does_not_advance_cursor_or_deliver(self):
        reader = Mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        reader.read_after.return_value = [self.row(server_id=0), self.row(local_id=12, server_id=101)]
        service = service_mock()
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        runner.tick()
        self.assertEqual(runner.cursors[self.group]["message_0.db"], 10)
        service.handle.assert_not_called()

    def test_old_history_in_new_shard_is_not_replied_to(self):
        reader, service = Mock(), service_mock()
        reader.snapshot.return_value = {}
        reader.read_after.return_value = [replace(self.row(), create_time=100)]
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        runner.tick()
        self.assertEqual(runner.cursors[self.group]["message_0.db"], 11)
        service.handle.assert_not_called()

    def test_deny_and_reallow_takes_new_baseline(self):
        reader = Mock()
        reader.snapshot.side_effect = [{"message_0.db": 10}, {"message_0.db": 20}]
        reader.read_after.return_value = []
        runner = GatewayRunner(reader, Mock(), service_mock(), self.store, "bot", frozenset({self.group}))
        runner.initialize()
        self.store.deny_group(self.group)
        runner.tick()
        self.assertNotIn(self.group, runner.cursors)
        self.store.allow_group(self.group)
        runner.tick()
        self.assertEqual(runner.cursors[self.group], {"message_0.db": 20})

    def test_health_failure_stops_before_reading_or_sending(self):
        reader, native, service = Mock(), Mock(), service_mock()
        native.health.side_effect = GatewayError("native_unavailable")
        runner = GatewayRunner(reader, native, service, self.store, "bot", frozenset({self.group}))
        with self.assertRaises(GatewayError):
            runner.initialize()
        reader.snapshot.assert_not_called()
        service.handle.assert_not_called()

    def test_quiet_poll_flushes_due_natural_reply_and_reports_it(self):
        reader, service = Mock(), service_mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        reader.read_after.return_value = []
        service.flush_pending.return_value = [DeliveryResult(ProcessResult(Outcome.ANSWERED, "回答"), DeliveryOutcome.CONFIRMED)]
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}))
        runner.initialize()
        counts = runner.tick()
        self.assertEqual(counts["received"], 0)
        self.assertEqual(counts["naturalEvaluated"], 1)
        self.assertEqual(counts["confirmed"], 1)

    def test_native_pat_missing_db_sender_is_resolved_from_verified_actor(self):
        reader, service = Mock(), service_mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        body = ('<msg><appmsg appid="" sdkver="0"><type>62</type><patinfo>'
                '<fromusername>member</fromusername><chatusername>fixture@chatroom</chatusername>'
                '<pattedusername>bot</pattedusername><template>ignored display text</template>'
                '</patinfo></appmsg><fromusername/></msg>')
        reader.read_after.return_value = [replace(self.row(), sender_id="", local_type=(62 << 32) | 49, text=body)]
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        counts = runner.tick()
        self.assertEqual(service.handle.call_count, 1)
        incoming = service.handle.call_args.args[0]
        self.assertEqual(incoming.sender_id, "member")
        self.assertEqual(incoming.interaction, "pat")
        self.assertFalse(incoming.mentioned_bot)
        self.assertTrue(incoming.is_directed)
        self.assertNotIn("ignored display text", incoming.text)
        self.assertEqual(counts["patReceived"], 1)

    def test_pending_pat_waits_for_server_id_before_advancing_cursor(self):
        reader, service = Mock(), service_mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        body = ('<msg><appmsg><type>62</type><patinfo><fromusername>member</fromusername>'
                '<chatusername>fixture@chatroom</chatusername><pattedusername>bot</pattedusername>'
                '</patinfo></appmsg></msg>')
        pending = replace(self.row(), server_id=0, sender_id="", local_type=(62 << 32) | 49, text=body)
        reader.read_after.return_value = [pending]
        runner = GatewayRunner(reader, Mock(), service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        runner.tick()
        self.assertEqual(runner.cursors[self.group]["message_0.db"], 10)
        service.handle.assert_not_called()
        reader.read_after.return_value = [replace(pending, server_id=101)]
        self.assertEqual(runner.tick()["patReceived"], 1)
        self.assertEqual(runner.cursors[self.group]["message_0.db"], 11)
        service.handle.assert_called_once()

    def test_sender_requires_new_matching_self_record_with_server_id(self):
        reader, native = Mock(), Mock()
        native.send_text.return_value = {"ret": 0}
        old = replace(self.row(sender="bot"), text="回答")
        wrong = replace(old, local_id=12, server_id=102, sender_id="other")
        confirmed = replace(old, local_id=13, server_id=103)
        reader.recent.side_effect = [[old], [old, wrong, confirmed]]
        sender = NativeSender(reader, native, self.store, "bot", frozenset({self.group}),
                              clock=lambda: 1005, sleep=lambda _: None)
        receipt = sender.send_group_text(self.group, "回答")
        self.assertTrue(receipt.confirmed)
        self.assertEqual(receipt.server_message_id, "103")
        native.send_text.assert_called_once_with(self.group, "回答")

    def test_unlisted_send_is_rejected_before_native_call(self):
        native = Mock()
        sender = NativeSender(Mock(), native, self.store, "bot", frozenset({self.group}))
        with self.assertRaises(GatewayError):
            sender.send_group_text("other@chatroom", "hello")
        native.send_text.assert_not_called()

    def test_sender_unknown_never_retries_native_submission(self):
        reader, native = Mock(), Mock()
        reader.recent.return_value = []
        native.send_text.side_effect = TimeoutError()
        sender = NativeSender(reader, native, self.store, "bot", frozenset({self.group}))
        receipt = sender.send_group_text(self.group, "回答")
        self.assertFalse(receipt.confirmed)
        native.send_text.assert_called_once()

    def test_fatal_identity_change_stops_current_batch_after_first_model_call(self):
        reader, native, model = Mock(), Mock(), Mock()
        reader.snapshot.return_value = {"message_0.db": 10}
        reader.read_after.return_value = [self.row(), self.row(local_id=12, server_id=101)]
        reader.recent.return_value = []
        model.complete.return_value = "你好"
        native.send_text.side_effect = GatewayFatalError("native_account_or_version_mismatch")
        service = DeliveryService(MessageProcessor(self.store, ChatService(model), cooldown_seconds=0),
            self.store, NativeSender(reader, native, self.store, "bot", frozenset({self.group})))
        runner = GatewayRunner(reader, native, service, self.store, "bot", frozenset({self.group}), clock=lambda: 1005)
        runner.initialize()
        with self.assertRaises(GatewayFatalError):
            runner.tick()
        self.assertEqual(model.complete.call_count, 1)
        self.assertEqual(native.send_text.call_count, 1)
        self.assertEqual(self.store.connection.execute("SELECT status FROM deliveries").fetchall(), [("unconfirmed",)])

    def test_second_gateway_cannot_take_existing_os_lock(self):
        lock = self.root / "worker.lock"
        with SingleInstance(lock):
            with self.assertRaises(GatewayError):
                with SingleInstance(lock):
                    self.fail("second instance started")
        with SingleInstance(lock):
            pass


class EncryptedReaderTests(unittest.TestCase):
    def test_reads_scoped_encrypted_shard_with_native_source(self):
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError:
            self.skipTest("native dependencies are not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            message_dir = root / "db_storage/message"
            message_dir.mkdir(parents=True)
            path = message_dir / "message_0.db"
            salt = bytes.fromhex("22" * 16)
            passphrase = "11" * 32
            key = hashlib.pbkdf2_hmac("sha512", bytes.fromhex(passphrase), salt, 256000, 32)
            connection = sqlcipher.connect(str(path))
            connection.execute(f'''PRAGMA key = "x'{key.hex()}{salt.hex()}'"''')
            connection.execute("CREATE TABLE Name2Id (user_name TEXT)")
            connection.execute("INSERT INTO Name2Id VALUES ('member')")
            table = "Msg_" + hashlib.md5(b"fixture@chatroom").hexdigest()
            connection.execute(f'CREATE TABLE "{table}" (local_id INTEGER, server_id INTEGER, local_type INTEGER, create_time INTEGER, real_sender_id INTEGER, message_content TEXT, source BLOB)')
            connection.execute(f'INSERT INTO "{table}" VALUES (1, 101, 1, 1000, 1, ?, ?)',
                               ("你好", b"<msgsource><atuserlist>bot</atuserlist></msgsource>"))
            connection.commit()
            connection.close()
            reader = EncryptedMessageReader(root, passphrase, "bot", frozenset({"fixture@chatroom"}))
            self.assertEqual(reader.snapshot("fixture@chatroom"), {"message_0.db": 1})
            rows = reader.read_after("fixture@chatroom", {"message_0.db": 0})
            self.assertEqual(rows[0].text, "你好")
            self.assertEqual(rows[0].sender_id, "member")
            with self.assertRaises(GatewayError):
                reader.snapshot("unauthorized@chatroom")


if __name__ == "__main__":
    unittest.main()
