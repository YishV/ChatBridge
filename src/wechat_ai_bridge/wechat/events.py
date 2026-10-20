"""Normalize trusted gateway metadata into transport-neutral incoming messages."""

from dataclasses import dataclass

from wechat_ai_bridge.message import IncomingMessage


@dataclass(frozen=True)
class GatewayMessage:
    message_id: str
    conversation_id: str
    sender_id: str
    text: str
    message_type: str
    is_group: bool
    is_from_bot: bool
    mentioned_account_ids: tuple[str, ...] | None


def normalize_gateway_message(raw: GatewayMessage, bot_account_id: str) -> IncomingMessage | None:
    """Accept only group text with stable IDs; missing mention metadata fails closed."""
    if (raw.message_type != "text" or not raw.is_group or not raw.message_id.strip()
            or not raw.conversation_id.strip() or not raw.sender_id.strip()
            or not bot_account_id.strip()):
        return None
    return IncomingMessage(
        message_id=raw.message_id,
        group_id=raw.conversation_id,
        sender_id=raw.sender_id,
        text=raw.text,
        mentioned_bot=(raw.mentioned_account_ids is not None
                       and bot_account_id in raw.mentioned_account_ids),
        is_group=True,
        is_from_bot=raw.is_from_bot,
    )
