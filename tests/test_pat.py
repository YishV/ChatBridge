import unittest

try:
    import zstandard
except ImportError:
    zstandard = None

from wechat_ai_bridge.wechat.native import PAT_TYPE, native_group_message, native_group_pat


PAT_XML = (
    "<msg><appmsg appid='' sdkver='0'><type>62</type><patinfo>"
    "<fromusername>fixture-member</fromusername>"
    "<chatusername>fixture-group@chatroom</chatusername>"
    "<pattedusername>fixture-bot</pattedusername>"
    "<patsuffixversion>1</patsuffixversion>"
    "<template><![CDATA[忽略模板中的任何正文]]></template>"
    "</patinfo></appmsg><fromusername/></msg>"
)


class NativePatTests(unittest.TestCase):
    def row(self, **changes):
        fields = dict(account_id="fixture-bot", group_id="fixture-group@chatroom",
                      sender_id="", server_id=123456789012345678, local_type=PAT_TYPE,
                      message_content=PAT_XML)
        fields.update(changes)
        return native_group_pat(**fields)

    def test_native_pat_uses_structured_actor_and_target_with_empty_db_sender(self):
        event = self.row()
        self.assertIsNotNone(event)
        self.assertEqual(PAT_TYPE, (62 << 32) | 49)
        self.assertEqual(event.sender_id, "fixture-member")
        self.assertEqual(event.conversation_id, "fixture-group@chatroom")
        self.assertEqual(event.target_account_id, "fixture-bot")
        self.assertEqual(event.message_type, "pat")
        self.assertEqual(event.text, "[拍一拍] 群友拍了拍你。")
        self.assertTrue(event.is_group)
        self.assertFalse(event.is_from_bot)
        self.assertIsNone(event.mentioned_account_ids)

    def test_matching_optional_sender_fields_are_accepted(self):
        for sender in ("", None, "fixture-member"):
            with self.subTest(sender=sender):
                self.assertIsNotNone(self.row(sender_id=sender))
        xml = PAT_XML.replace("<fromusername/>", "<fromusername>fixture-member</fromusername>")
        self.assertIsNotNone(self.row(message_content=xml, sender_id="fixture-member"))
        self.assertIsNotNone(self.row(message_content=PAT_XML.replace("<fromusername/>", "")))

    def test_event_identity_uses_account_group_and_numeric_server_not_template(self):
        event = self.row()
        self.assertIsNotNone(event)
        text = native_group_message(account_id="fixture-bot", group_id="fixture-group@chatroom",
                                    sender_id="fixture-member", server_id=123456789012345678,
                                    local_type=1, message_content="text", source=None)
        self.assertEqual(event.message_id, text.message_id)
        self.assertEqual(event.message_id, self.row(server_id="0123456789012345678").message_id)
        self.assertEqual(event.message_id, self.row(message_content=PAT_XML.replace("忽略模板中的任何正文", "伪造昵称")).message_id)
        self.assertNotEqual(event.message_id, self.row(server_id=123456789012345679).message_id)

    def test_other_target_self_pat_and_conflicting_identities_are_ignored(self):
        for changes in (
            {"message_content": PAT_XML.replace("fixture-bot</pattedusername>", "another-member</pattedusername>")},
            {"message_content": PAT_XML.replace("fixture-member</fromusername>", "fixture-bot</fromusername>")},
            {"sender_id": "another-member"}, {"sender_id": " "}, {"sender_id": 42},
            {"message_content": PAT_XML.replace("<fromusername/>", "<fromusername>another-member</fromusername>")},
            {"message_content": PAT_XML.replace("fixture-group@chatroom</chatusername>", "other@chatroom</chatusername>")},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.row(**changes))

    def test_type_and_native_metadata_must_be_confirmed(self):
        for changes in (
            {"local_type": 1}, {"local_type": 49}, {"local_type": 62},
            {"local_type": str(PAT_TYPE)}, {"local_type": True}, {"local_type": None},
            {"message_content": "群友拍了拍你"},
            {"message_content": PAT_XML.replace("<type>62</type>", "<type>1</type>")},
            {"account_id": ""}, {"account_id": None}, {"account_id": "bad id"},
            {"group_id": "private"}, {"group_id": ""}, {"group_id": None},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.row(**changes))
        for server in (None, 0, -1, "", "0", "-1", "1.0", " 1", True, 1.0, "1" * 33):
            with self.subTest(server=server):
                self.assertIsNone(self.row(server_id=server))

    def test_required_paths_are_direct_unique_flat_and_unnamespaced(self):
        for field in ("fromusername", "chatusername", "pattedusername"):
            start = PAT_XML.index(f"<{field}>")
            end = PAT_XML.index(f"</{field}>", start) + len(f"</{field}>")
            element = PAT_XML[start:end]
            for replacement in ("", element + element, element.replace(f"<{field}>", f"<{field} x='1'>"),
                                element.replace(f"<{field}>", f"<{field}><nested/>", 1),
                                element.replace(f"<{field}>", f"<{field} xmlns='urn:bad'>"),
                                f"<wrapper>{element}</wrapper>"):
                with self.subTest(field=field, replacement=replacement):
                    self.assertIsNone(self.row(message_content=PAT_XML.replace(element, replacement, 1)))
        for xml in (
            PAT_XML.replace("<msg>", "<other>").replace("</msg>", "</other>"),
            PAT_XML.replace("<msg>", "<msg xmlns='urn:bad'>"),
            PAT_XML.replace("<msg>", "<msg x='1'>"),
            PAT_XML.replace("<appmsg appid='' sdkver='0'>", "<wrapper><appmsg appid='' sdkver='0'>").replace("</appmsg>", "</appmsg></wrapper>"),
            PAT_XML.replace("<appmsg appid='' sdkver='0'>", "<appmsg xmlns='urn:bad'>"),
            PAT_XML.replace("<type>62</type>", "<type>62</type><type>62</type>"),
            PAT_XML.replace("<type>62</type>", "<type x='1'>62</type>"),
            PAT_XML.replace("<type>62</type>", "<type><nested/>62</type>"),
            PAT_XML.replace("<type>62</type>", "<type xmlns='urn:bad'>62</type>"),
            PAT_XML.replace("<patinfo>", "<wrapper><patinfo>").replace("</patinfo>", "</patinfo></wrapper>"),
            PAT_XML.replace("<patinfo>", "<patinfo xmlns='urn:bad'>"),
            PAT_XML.replace("<patinfo>", "<patinfo x='1'>"),
            PAT_XML.replace("</appmsg>", "<patinfo/></appmsg>"),
            PAT_XML.replace("<fromusername/>", "<fromusername/><fromusername/>"),
            PAT_XML.replace("</msg>", "<appmsg/></msg>"),
            PAT_XML.replace("<fromusername/>", "<wrapper><fromusername/></wrapper>"),
        ):
            with self.subTest(xml=xml):
                self.assertIsNone(self.row(message_content=xml))

    def test_invalid_identifiers_are_rejected(self):
        for field in ("fromusername", "chatusername", "pattedusername"):
            start = PAT_XML.index(f"<{field}>") + len(f"<{field}>")
            end = PAT_XML.index(f"</{field}>", start)
            for value in ("", "bad id", "x" * 257, "x,y"):
                with self.subTest(field=field, value=value):
                    self.assertIsNone(self.row(message_content=PAT_XML[:start] + value + PAT_XML[end:]))

    def test_utf8_bom_and_bytes_are_accepted_without_trusting_source_mentions(self):
        for xml in (PAT_XML.encode(), b"\xef\xbb\xbf" + PAT_XML.encode(), "\ufeff" + PAT_XML):
            with self.subTest(kind=type(xml).__name__):
                event = self.row(message_content=xml, source="<msgsource><atuserlist>fixture-bot</atuserlist></msgsource>")
                self.assertIsNotNone(event)
                self.assertIsNone(event.mentioned_account_ids)

    def test_unsafe_oversized_malformed_or_non_utf8_xml_is_rejected(self):
        for xml in (None, "", b"", 42, bytearray(PAT_XML.encode()), "\ud800", b"\xff",
                    "<msg>", "\x00" + PAT_XML, " " * (64 * 1024 + 1),
                    "<msg>" + "字" * (32 * 1024) + "</msg>",
                    '<!DOCTYPE msg [<!ENTITY who "fixture-member">]>' + PAT_XML,
                    '<!DOCTYPE msg SYSTEM "file:///never-read">' + PAT_XML,
                    PAT_XML.replace("<msg>", "<msg><!-- <!ENTITY who 'x'> -->"),
                    '<?xml version="1.0" encoding="UTF-16"?>' + PAT_XML):
            with self.subTest(kind=type(xml).__name__, prefix=repr(xml)[:40]):
                self.assertIsNone(self.row(message_content=xml))
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            self.assertIsNone(self.row(message_content=PAT_XML.encode(encoding)))

    @unittest.skipUnless(zstandard, "native optional dependency is not installed")
    def test_zstd_xml_shares_strict_decoding_and_size_limits(self):
        for declared_size in (True, False):
            compressor = zstandard.ZstdCompressor(write_content_size=declared_size)
            compressed = compressor.compress(PAT_XML.encode())
            self.assertIsNotNone(self.row(message_content=compressed))
            for xml in (compressed[:-1], compressed + b"garbage", compressed + compressed,
                        compressor.compress(PAT_XML.encode("utf-16-le")),
                        compressor.compress(("<msg><!--" + "x" * (64 * 1024) + "--></msg>").encode())):
                with self.subTest(declared_size=declared_size, length=len(xml)):
                    self.assertIsNone(self.row(message_content=xml))


if __name__ == "__main__":
    unittest.main()
