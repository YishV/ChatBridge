import unittest

from wechat_ai_bridge.wechat.events import GatewayMessage, normalize_gateway_message


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

    def test_private_and_non_text_are_discarded(self):
        private = GatewayMessage("m1", "person", "member", "hello", "text", False, False, None)
        image = GatewayMessage("m2", "test@chatroom", "member", "", "image", True, False, None)
        self.assertIsNone(normalize_gateway_message(private, "bot-wxid"))
        self.assertIsNone(normalize_gateway_message(image, "bot-wxid"))


if __name__ == "__main__":
    unittest.main()
