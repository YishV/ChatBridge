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
    target_account_id: str | None = None


def normalize_gateway_message(raw: GatewayMessage, bot_account_id: str) -> IncomingMessage | None:
    """Accept group text or a validated pat directed at the current account."""
    if (raw.message_type not in ("text", "pat") or not raw.is_group or not raw.message_id.strip()
            or not raw.conversation_id.strip() or not raw.sender_id.strip()
            or not bot_account_id.strip()):
        return None
    is_pat = raw.message_type == "pat"
    if is_pat and (raw.target_account_id != bot_account_id or raw.is_from_bot or raw.sender_id == bot_account_id):
        return None
    return IncomingMessage(
        message_id=raw.message_id,
        group_id=raw.conversation_id,
        sender_id=raw.sender_id,
        text=raw.text,
        mentioned_bot=(not is_pat and raw.mentioned_account_ids is not None
                       and bot_account_id in raw.mentioned_account_ids),
        is_group=True,
        is_from_bot=raw.is_from_bot,
        interaction="pat" if is_pat else "text",
    )
