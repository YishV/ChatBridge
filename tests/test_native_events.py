import unittest

try:
    import zstandard
except ImportError:
    zstandard = None

from wechat_ai_bridge.wechat.events import normalize_gateway_message
from wechat_ai_bridge.wechat.native import native_group_message, parse_native_mentions


SOURCE_LIMIT = 64 * 1024
MENTION_SOURCE = "<msgsource><atuserlist><![CDATA[legacy_bot,member-a,legacy_bot]]></atuserlist></msgsource>"
PLAIN_SOURCE = "<msgsource><silence>0</silence></msgsource>"


class NativeMentionTests(unittest.TestCase):
    def test_direct_native_mentions_are_deduplicated_in_source_order(self):
        self.assertEqual(parse_native_mentions(MENTION_SOURCE), ("legacy_bot", "member-a"))

    def test_valid_source_without_mentions_is_known_empty(self):
        for source in ("<msgsource/>", PLAIN_SOURCE, "<msgsource><atuserlist/></msgsource>"):
            with self.subTest(source=source):
                self.assertEqual(parse_native_mentions(source), ())

    def test_utf8_bytes_and_comma_whitespace(self):
        source = "<msgsource><atuserlist> legacy_bot , member-a </atuserlist></msgsource>"
        self.assertEqual(parse_native_mentions(source.encode("utf-8")), ("legacy_bot", "member-a"))

    def test_missing_malformed_and_wrong_types_are_unknown(self):
        for source in (None, "", b"", "<msgsource>", b"\xff", "\ud800", 4, bytearray(b"<msgsource/>")):
            with self.subTest(kind=type(source).__name__):
                self.assertIsNone(parse_native_mentions(source))

    def test_unknown_xml_structure_is_rejected(self):
        for source in (
            "<other><atuserlist>legacy_bot</atuserlist></other>",
            "<msgsource xmlns='urn:unknown'><atuserlist>legacy_bot</atuserlist></msgsource>",
            "<msgsource><wrapper><atuserlist>legacy_bot</atuserlist></wrapper></msgsource>",
            "<msgsource><atuserlist><account>legacy_bot</account></atuserlist></msgsource>",
            "<msgsource><atuserlist>legacy_bot</atuserlist><atuserlist>member-a</atuserlist></msgsource>",
            "<msgsource><atuserlist unexpected='1'>legacy_bot</atuserlist></msgsource>",
            "<msgsource><atuserlist xmlns='urn:unknown'>legacy_bot</atuserlist></msgsource>",
            "<msgsource>unstructured text</msgsource>",
        ):
            with self.subTest(source=source):
                self.assertIsNone(parse_native_mentions(source))

    def test_dtd_and_entity_declarations_are_rejected(self):
        for source in (
            '<!DOCTYPE msgsource [<!ENTITY who "legacy_bot">]><msgsource><atuserlist>&who;</atuserlist></msgsource>',
            '<!DOCTYPE msgsource SYSTEM "file:///never-read"><msgsource/>',
            '<msgsource><!-- <!ENTITY who "legacy_bot"> --></msgsource>',
        ):
            with self.subTest(source=source):
                self.assertIsNone(parse_native_mentions(source))

    def test_invalid_mention_identifiers_are_unknown(self):
        for value in ("legacy_bot,,member-a", "legacy_bot;member-a", "legacy bot", "<bad>", "x" * 257):
            with self.subTest(value=value[:30]):
                self.assertIsNone(parse_native_mentions(f"<msgsource><atuserlist>{value}</atuserlist></msgsource>"))

    def test_source_limits_apply_to_bytes_and_utf8_length(self):
        self.assertIsNone(parse_native_mentions(b" " * (SOURCE_LIMIT + 1)))
        self.assertIsNone(parse_native_mentions(" " * (SOURCE_LIMIT + 1)))
        self.assertIsNone(parse_native_mentions("<msgsource>" + "字" * (SOURCE_LIMIT // 2) + "</msgsource>"))

    def test_non_utf8_xml_encoding_declaration_is_rejected(self):
        self.assertIsNone(parse_native_mentions('<?xml version="1.0" encoding="UTF-16"?><msgsource/>'))

    def test_utf16_without_bom_cannot_bypass_entity_rejection(self):
        xml = '<!DOCTYPE msgsource [<!ENTITY who "legacy_bot">]><msgsource><atuserlist>&who;</atuserlist></msgsource>'
        for encoding in ("utf-16-le", "utf-16-be"):
            raw = xml.encode(encoding)
            for source in (raw, raw.decode("utf-8")):
                with self.subTest(encoding=encoding, kind=type(source).__name__):
                    self.assertIsNone(parse_native_mentions(source))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_utf16_without_bom_cannot_bypass_entity_rejection(self):
        xml = '<!DOCTYPE msgsource [<!ENTITY who "legacy_bot">]><msgsource><atuserlist>&who;</atuserlist></msgsource>'
        for encoding in ("utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                compressed = zstandard.ZstdCompressor().compress(xml.encode(encoding))
                self.assertIsNone(parse_native_mentions(compressed))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_native_source(self):
        compressed = zstandard.ZstdCompressor().compress(MENTION_SOURCE.encode())
        self.assertEqual(parse_native_mentions(compressed), ("legacy_bot", "member-a"))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_without_declared_size(self):
        compressed = zstandard.ZstdCompressor(write_content_size=False).compress(MENTION_SOURCE.encode())
        self.assertEqual(parse_native_mentions(compressed), ("legacy_bot", "member-a"))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_truncated_corrupt_and_trailing_data_are_unknown(self):
        compressed = zstandard.ZstdCompressor().compress(MENTION_SOURCE.encode())
        for source in (b"\x28\xb5\x2f\xfdgarbage", compressed[:-1], compressed + b"garbage", compressed + compressed):
            with self.subTest(length=len(source)):
                self.assertIsNone(parse_native_mentions(source))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_decompressed_limit_with_and_without_declared_size(self):
        oversized = ("<msgsource><!--" + "x" * SOURCE_LIMIT + "--></msgsource>").encode()
        for declared_size in (True, False):
            with self.subTest(declared_size=declared_size):
                compressed = zstandard.ZstdCompressor(write_content_size=declared_size).compress(oversized)
                self.assertLess(len(compressed), SOURCE_LIMIT)
                self.assertIsNone(parse_native_mentions(compressed))


class NativeGroupMessageTests(unittest.TestCase):
    def row(self, **changes):
        fields = dict(account_id="legacy_bot", group_id="fixture-a@chatroom", sender_id="member-a",
                      server_id=987654321012345678, local_type=1, message_content="@同一显示名 同一正文",
                      source=MENTION_SOURCE)
        fields.update(changes)
        return native_group_message(**fields)

    def test_identical_bodies_only_native_source_controls_mentions(self):
        native = self.row()
        typed = self.row(source=PLAIN_SOURCE)
        self.assertEqual(native.text, typed.text)
        self.assertTrue(normalize_gateway_message(native, "legacy_bot").mentioned_bot)
        self.assertFalse(normalize_gateway_message(typed, "legacy_bot").mentioned_bot)

    def test_missing_source_stays_unknown_and_cannot_trigger(self):
        row = self.row(source=None)
        self.assertIsNone(row.mentioned_account_ids)
        self.assertFalse(normalize_gateway_message(row, "legacy_bot").mentioned_bot)

    def test_stable_server_id_scoped_by_account_and_group(self):
        original = self.row()
        self.assertEqual(original.message_id, self.row(server_id="0987654321012345678", message_content="正文更新").message_id)
        self.assertNotEqual(original.message_id, self.row(account_id="other-account").message_id)
        self.assertNotEqual(original.message_id, self.row(group_id="fixture-b@chatroom").message_id)
        self.assertNotEqual(original.message_id, self.row(server_id=987654321012345679).message_id)

    def test_sender_is_self_only_on_exact_account_match(self):
        self.assertTrue(self.row(sender_id="legacy_bot").is_from_bot)
        self.assertFalse(self.row(sender_id="legacy_bot_other").is_from_bot)

    def test_mapping_keeps_explicit_group_sender_and_full_body(self):
        body = "  " + "内容" * 200 + "  "
        row = self.row(message_content=body)
        self.assertEqual(row.conversation_id, "fixture-a@chatroom")
        self.assertEqual(row.sender_id, "member-a")
        self.assertEqual(row.text, body)
        self.assertEqual(row.message_type, "text")
        self.assertTrue(row.is_group)

    def test_unconfirmed_or_invalid_server_ids_are_rejected(self):
        for value in (None, 0, -1, "", "0", "-1", "1.0", " 1", True, 1.0, "1" * 33):
            with self.subTest(value=value):
                self.assertIsNone(self.row(server_id=value))

    def test_private_non_text_and_missing_required_fields_are_rejected(self):
        for changes in (
            {"group_id": "private-person"}, {"group_id": "fixture@chatroom.extra"},
            {"group_id": ""}, {"group_id": None}, {"account_id": ""}, {"account_id": None},
            {"sender_id": ""}, {"sender_id": "member a"}, {"sender_id": None},
            {"local_type": 3}, {"local_type": "1"}, {"local_type": True}, {"local_type": None},
            {"message_content": None}, {"message_content": b"text"}, {"message_content": " "},
        ):
            with self.subTest(fields=tuple(changes)):
                self.assertIsNone(self.row(**changes))


if __name__ == "__main__":
    unittest.main()
