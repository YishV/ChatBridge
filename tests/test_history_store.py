import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from chat_bridge.state.store import StateStore


def chat(content="测试正文\n第二行", **changes):
    value = {"member_id": "member_012345abcdef", "sender_name": "测试昵称",
             "content": content, "interaction": "text"}
    value.update(changes)
    return value


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.store.allow_group("group-a")

    def rows(self):
        return self.store.connection.execute("SELECT * FROM chat_history").fetchall()

    def test_new_database_creates_history_schema_and_index(self):
        self.assertFalse(self.store.has_chat_history())
        columns = self.store.connection.execute("PRAGMA table_info(chat_history)").fetchall()
        self.assertEqual([column[1] for column in columns],
                         ["id", "message_id", "group_id", "role", "member_id", "sender_name",
                          "content", "interaction", "created_at"])
        indexes = self.store.connection.execute("PRAGMA index_list(chat_history)").fetchall()
        self.assertTrue(any(
            [(row[2], row[3]) for row in self.store.connection.execute(
                f'PRAGMA index_xinfo("{index[1]}")') if row[5]] == [("group_id", 0), ("id", 1)]
            for index in indexes))

    def test_old_database_upgrade_is_idempotent_and_preserves_tables(self):
        path = Path(self.temp.name) / "old.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE messages (message_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, created_at TEXT NOT NULL)")
            connection.execute("INSERT INTO messages VALUES ('old-message', 'old-group', '2000-01-01')")
            connection.execute("CREATE TABLE custom_table (value TEXT)")
            connection.execute("INSERT INTO custom_table VALUES ('preserved')")
        for _ in range(2):
            store = StateStore(path)
            try:
                self.assertFalse(store.has_chat_history())
                self.assertFalse(store.claim_event("old-message", "old-group"))
                self.assertEqual(store.connection.execute("SELECT value FROM custom_table").fetchone(),
                                 ("preserved",))
                self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            finally:
                store.close()

    def test_claim_saves_verbatim_body_with_outer_ids_and_fixed_user_role(self):
        value = chat("第一行\n' ; DROP TABLE groups; --", role="assistant",
                     message_id="forged-message", group_id="forged-group", interaction="pat")
        self.assertTrue(self.store.claim_event("m1", "group-a", chat=value))
        row = self.store.load_chat_history("group-a")[0]
        self.assertEqual({key: row[key] for key in value if key not in {"role", "message_id", "group_id"}},
                         {key: value[key] for key in value if key not in {"role", "message_id", "group_id"}})
        self.assertEqual((row["role"], row["message_id"], row["group_id"]), ("user", "m1", "group-a"))
        self.assertEqual(datetime.fromisoformat(row["created_at"]).utcoffset(), timezone.utc.utcoffset(None))
        self.assertTrue(self.store.is_allowed("group-a"))
        self.assertTrue(self.store.has_chat_history())

    def test_duplicate_claim_does_not_insert_another_history_row(self):
        self.assertTrue(self.store.claim_event("m1", "group-a", chat=chat()))
        self.assertFalse(self.store.claim_event("m1", "group-a", chat=chat("different")))
        self.assertEqual(len(self.rows()), 1)

    def test_history_insert_failure_rolls_back_event_claim(self):
        self.store.connection.execute(
            "CREATE TRIGGER reject_history BEFORE INSERT ON chat_history BEGIN SELECT RAISE(ABORT, 'test_failure'); END")
        self.store.connection.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.claim_event("m1", "group-a", chat=chat())
        self.assertIsNone(self.store.connection.execute(
            "SELECT 1 FROM seen_events WHERE message_id = 'm1'").fetchone())
        self.assertEqual(self.rows(), [])
        self.store.connection.execute("DROP TRIGGER reject_history")
        self.store.connection.commit()
        self.assertTrue(self.store.claim_event("m1", "group-a", chat=chat()))

    def test_confirmed_delivery_commits_history_once_using_delivery_group(self):
        self.store.begin_delivery("m1", "group-a")
        value = chat("确认回复", group_id="forged-group", role="user", message_id="forged-message")
        self.store.finish_delivery("m1", confirmed=True, server_message_id="server-1", chat=value)
        self.store.finish_delivery("m1", confirmed=True, server_message_id="server-2", chat=chat("other"))
        self.assertEqual(self.store.delivery_status("m1"), "confirmed")
        row = self.store.load_chat_history("group-a")[0]
        self.assertEqual((row["message_id"], row["group_id"], row["role"], row["content"]),
                         ("m1", "group-a", "assistant", "确认回复"))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.store.connection.execute(
            "SELECT server_message_id FROM deliveries WHERE message_id = 'm1'").fetchone(), ("server-1",))

    def test_history_insert_failure_keeps_delivery_pending_and_blocks_resend(self):
        self.store.begin_delivery("m1", "group-a")
        self.store.connection.execute(
            "CREATE TRIGGER reject_history BEFORE INSERT ON chat_history BEGIN SELECT RAISE(ABORT, 'test_failure'); END")
        self.store.connection.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.finish_delivery("m1", confirmed=True, server_message_id="server-1", chat=chat())
        self.assertEqual(self.store.delivery_status("m1"), "pending")
        self.assertFalse(self.store.begin_delivery("m1", "group-a"))
        self.assertEqual(self.rows(), [])

    def test_unknown_or_unconfirmed_deliveries_never_record_assistant(self):
        self.store.finish_delivery("unknown", confirmed=True, server_message_id="server-1", chat=chat())
        for index, (confirmed, server_id) in enumerate([(False, None), (False, "server-1"), (True, None)]):
            message_id = f"m{index}"
            self.store.begin_delivery(message_id, "group-a")
            self.store.finish_delivery(message_id, confirmed=confirmed, server_message_id=server_id, chat=chat())
            self.store.finish_delivery(message_id, confirmed=True, server_message_id="late-server", chat=chat())
            self.assertEqual(self.store.delivery_status(message_id), "unconfirmed")
        self.assertEqual(self.rows(), [])

    def test_legacy_calls_do_not_create_history(self):
        self.assertTrue(self.store.claim_event("m1", "group-a"))
        self.store.begin_delivery("m1", "group-a")
        self.store.finish_delivery("m1", confirmed=True, server_message_id="server-1")
        self.assertEqual(self.store.delivery_status("m1"), "confirmed")
        self.assertFalse(self.store.has_chat_history())

    def test_load_uses_latest_rows_in_original_order_and_isolates_groups(self):
        self.store.allow_group("group-b")
        for index in range(60):
            self.store.claim_event(f"a{index}", "group-a", chat=chat(str(index)))
            self.store.claim_event(f"b{index}", "group-b", chat=chat("other"))
        self.store.connection.execute("UPDATE chat_history SET created_at = '2000-01-01T00:00:00+00:00'")
        self.store.connection.commit()
        loaded = self.store.load_chat_history("group-a")
        self.assertEqual([row["content"] for row in loaded], [str(index) for index in range(10, 60)])
        self.assertEqual([row["id"] for row in loaded], sorted(row["id"] for row in loaded))
        self.assertEqual([row["content"] for row in self.store.load_chat_history("group-a", 2)], ["58", "59"])
        self.assertEqual(len(self.store.load_chat_history("group-a", 200)), 60)

    def test_revoked_group_cannot_load_but_history_survives_reauthorization_and_restart(self):
        self.store.claim_event("m1", "group-a", chat=chat())
        self.store.deny_group("group-a")
        self.assertEqual(self.store.load_chat_history("group-a"), [])
        self.assertTrue(self.store.has_chat_history())
        other = StateStore(self.path)
        try:
            self.assertEqual(other.load_chat_history("group-a"), [])
            other.allow_group("group-a")
            self.assertEqual(other.load_chat_history("group-a")[0]["content"], chat()["content"])
        finally:
            other.close()

    def test_load_rejects_invalid_limits_without_echoing_input(self):
        for limit in [0, -1, 201, True, False, 1.0, "private_limit", None]:
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, "^invalid_history_limit$"):
                self.store.load_chat_history("group-a", limit)

    def test_invalid_chat_is_rejected_without_consuming_event_or_delivery(self):
        invalid = [None, "raw-sender", "member_012345abcdeg", "member_012345abcde", 42]
        values = [chat(member_id=value) for value in invalid]
        values += [chat(content=value) for value in ["", 42, None, "中" * 21846, "x" * 65537, "\ud800"]]
        values += [chat(sender_name=value) for value in [42, "名" * 65, "\ud800"]]
        values += [chat(interaction=value) for value in ["image", None, 42]]
        values += [{}, [], "private-input"]
        for index, value in enumerate(values):
            message_id = f"invalid-{index}"
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, "^invalid_chat_history$"):
                    self.store.claim_event(message_id, "group-a", chat=value)
                self.assertIsNone(self.store.connection.execute(
                    "SELECT 1 FROM seen_events WHERE message_id = ?", (message_id,)).fetchone())
                self.store.begin_delivery(message_id, "group-a")
                with self.assertRaisesRegex(ValueError, "^invalid_chat_history$"):
                    self.store.finish_delivery(message_id, confirmed=True, server_message_id="server", chat=value)
                self.assertEqual(self.store.delivery_status(message_id), "pending")
        self.assertFalse(self.store.has_chat_history())

    def test_valid_chat_accepts_exact_byte_limit_and_optional_nickname(self):
        self.store.claim_event("m1", "group-a", chat=chat("x" * 65536, sender_name=None))
        self.store.claim_event("m2", "group-a", chat=chat("中" * 21845, sender_name="名" * 64))
        self.assertEqual([len(row["content"].encode("utf-8")) for row in self.store.load_chat_history("group-a")],
                         [65536, 65535])


if __name__ == "__main__":
    unittest.main()
