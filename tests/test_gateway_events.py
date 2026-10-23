import unittest

from chat_bridge.wechat.events import GatewayMessage, normalize_gateway_message
from chat_bridge.wechat.native import native_private_message


class GatewayEventTests(unittest.TestCase):
    def message(self, mentions=None, text="@机器人 请回答"):
        return GatewayMessage(
            message_id="wechat-message-1", conversation_id="test@chatroom",
            sender_id="member-1", text=text, message_type="text",
            is_group=True, is_from_bot=False, mentioned_account_ids=mentions,
        )

    def test_textual_nickname_without_mention_metadata_is_not_trigger(self):
        event = normalize_gateway_message(self.message(), "bot-wxid")
        self.assertFalse(event.mentioned_bot)

    def test_explicit_bot_id_is_trigger(self):
        event = normalize_gateway_message(self.message(("member-2", "bot-wxid")), "bot-wxid")
        self.assertTrue(event.mentioned_bot)

    def test_mention_of_another_member_is_not_trigger(self):
        event = normalize_gateway_message(self.message(("member-2",)), "bot-wxid")
        self.assertFalse(event.mentioned_bot)

    def test_private_native_text_is_directed_and_has_private_message_id(self):
        raw = native_private_message(account_id='bot', conversation_id='wxid_friend',
            sender_id='wxid_friend', server_id=123, local_type=1, message_content='在吗')
        self.assertIsNotNone(raw)
        event = normalize_gateway_message(raw, 'bot')
        self.assertIsNotNone(event)
        self.assertFalse(event.is_group)
        self.assertTrue(event.mentioned_bot)
        self.assertTrue(event.is_directed)
        self.assertTrue(event.message_id.startswith('wechat-private:'))

    def test_private_text_is_directed_without_mention_metadata(self):
        private = GatewayMessage("m1", "person", "member", "hello", "text", False, False, None)
        image = GatewayMessage("m2", "test@chatroom", "member", "", "image", True, False, None)
        event = normalize_gateway_message(private, "bot-wxid")
        self.assertIsNotNone(event)
        self.assertFalse(event.is_group)
        self.assertTrue(event.mentioned_bot)
        self.assertIsNone(normalize_gateway_message(image, "bot-wxid"))


if __name__ == "__main__":
    unittest.main()
