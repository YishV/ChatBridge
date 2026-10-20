import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_ai_bridge.wechat.errors import GatewayError
from wechat_ai_bridge.wechat.reader import EncryptedMessageReader


class RecordingConnection:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.queries = []
        self.closed = False

    def execute(self, statement, parameters=()):
        self.queries.append((statement, parameters))
        return self.connection.execute(statement, parameters)

    def close(self):
        self.closed = True
        self.connection.close()


class SenderNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.group = "fixture@chatroom"
        self.other_group = "second@chatroom"
        self.reader = EncryptedMessageReader(
            self.root, "0" * 64, "bot", frozenset({self.group, self.other_group}))
        self.path = self.root / "db_storage" / "contact" / "contact.db"
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("CREATE TABLE contact (username TEXT, nick_name, remark TEXT)")
        self.connections = []

        def connect(path):
            connection = RecordingConnection(path)
            self.connections.append(connection)
            return connection

        self.reader._connect = Mock(side_effect=connect)

    def contact(self, sender="member", nickname="微信昵称", remark="不得外发的备注"):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("INSERT INTO contact VALUES (?, ?, ?)", (sender, nickname, remark))

    def test_queries_only_one_sender_nickname_with_parameter(self):
        sender = "member' OR 1=1 --"
        self.contact(sender, "小明")
        self.contact("someone", "其他人")

        self.assertEqual(self.reader.sender_display_name(self.group, sender), "小明")

        self.reader._connect.assert_called_once_with(self.path.resolve())
        self.assertEqual(len(self.connections[0].queries), 1)
        statement, parameters = self.connections[0].queries[0]
        self.assertEqual(" ".join(statement.split()).lower(),
                         "select nick_name from contact where username=? limit 1")
        self.assertEqual(parameters, (sender,))
        self.assertTrue(self.connections[0].closed)

    def test_disallowed_groups_and_invalid_senders_do_not_connect(self):
        for group, sender in [("unknown@chatroom", "member"), ("private", "member"),
                              (self.group, ""), (self.group, " "), (self.group, "bot"),
                              (self.group, self.group), (self.group, "other@chatroom")]:
            with self.subTest(group=group, sender=sender):
                self.assertIsNone(self.reader.sender_display_name(group, sender))
        self.reader._connect.assert_not_called()

    def test_missing_contact_database_returns_none(self):
        self.path.unlink()

        self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.reader._connect.assert_not_called()

    def test_unreadable_database_returns_none(self):
        self.reader._connect.side_effect = GatewayError("database_unreadable")

        self.assertIsNone(self.reader.sender_display_name(self.group, "member"))

    def test_bad_schema_closes_connection_and_returns_none(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("DROP TABLE contact")
            connection.execute("CREATE TABLE contact (username TEXT, remark TEXT)")

        self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.assertTrue(self.connections[0].closed)

    def test_cleans_controls_collapses_whitespace_and_truncates(self):
        self.contact(nickname="\x00小\x1b明\n\t 同学\r\n" + "名" * 100)

        result = self.reader.sender_display_name(self.group, "member")

        self.assertEqual(result, ("小明 同学 " + "名" * 100)[:64])
        self.assertEqual(len(result), 64)

    def test_removes_invisible_format_controls(self):
        self.contact(nickname="小\u202e明\u200b\ufeff")

        self.assertEqual(self.reader.sender_display_name(self.group, "member"), "小明")

    def test_empty_non_text_or_missing_nickname_has_no_fallback(self):
        for index, nickname in enumerate([None, "", "\x00\n\t", 123, b"nickname"]):
            self.contact(str(index), nickname)
            with self.subTest(nickname=nickname):
                self.assertIsNone(self.reader.sender_display_name(self.group, str(index)))
        self.assertIsNone(self.reader.sender_display_name(self.group, "not-found"))

    def test_cache_reuses_name_until_expiry(self):
        self.contact(nickname="旧名字")
        with patch("time.monotonic", return_value=100):
            self.assertEqual(self.reader.sender_display_name(self.group, "member"), "旧名字")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("UPDATE contact SET nick_name='新名字'")
        with patch("time.monotonic", return_value=159):
            self.assertEqual(self.reader.sender_display_name(self.group, "member"), "旧名字")
        with patch("time.monotonic", return_value=160):
            self.assertEqual(self.reader.sender_display_name(self.group, "member"), "新名字")
        self.assertEqual(self.reader._connect.call_count, 2)

    def test_missing_name_is_cached_briefly(self):
        with patch("time.monotonic", return_value=100):
            self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
            self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.reader._connect.assert_called_once()

    def test_cache_is_scoped_to_group_and_rechecks_whitelist(self):
        self.contact(nickname="旧名字")
        self.assertEqual(self.reader.sender_display_name(self.group, "member"), "旧名字")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("UPDATE contact SET nick_name='新名字'")

        self.assertEqual(self.reader.sender_display_name(self.other_group, "member"), "新名字")
        self.reader.groups = frozenset()
        self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.assertEqual(self.reader._connect.call_count, 2)

    def test_cache_retains_at_most_500_senders(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.executemany("INSERT INTO contact VALUES (?, ?, ?)",
                                   [(str(index), f"成员{index}", "备注") for index in range(501)])
        with patch("time.monotonic", return_value=100):
            for index in range(501):
                self.assertEqual(self.reader.sender_display_name(self.group, str(index)), f"成员{index}")
            self.assertEqual(self.reader.sender_display_name(self.group, "0"), "成员0")
        self.assertEqual(self.reader._connect.call_count, 502)

    def test_contact_directory_resolving_outside_scope_is_rejected(self):
        original_resolve = Path.resolve
        contact_directory = self.path.parent

        def resolve(path, *args, **kwargs):
            if path == contact_directory:
                return self.root.parent
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.reader._connect.assert_not_called()

    def test_contact_file_resolving_outside_directory_is_rejected(self):
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == self.path:
                return self.root / "unrelated.db"
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            self.assertIsNone(self.reader.sender_display_name(self.group, "member"))
        self.reader._connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
