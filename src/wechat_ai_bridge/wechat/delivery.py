"""One-shot delivery boundary for a future Windows WeChat adapter."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage, ProcessResult
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.wechat.errors import GatewayFatalError


@dataclass(frozen=True)
class SendReceipt:
    confirmed: bool
    server_message_id: str | None = None


class GroupSender(Protocol):
    def send_group_text(self, group_id: str, text: str) -> SendReceipt: ...


class DeliveryOutcome(StrEnum):
    NOT_SENT = "not_sent"
    SKIPPED_PAUSED = "skipped_paused"
    SKIPPED_NOT_ALLOWED = "skipped_not_allowed"
    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class DeliveryResult:
    processed: ProcessResult
    delivery: DeliveryOutcome


class DeliveryService:
    def __init__(self, processor: MessageProcessor, store: StateStore, sender: GroupSender):
        self.processor = processor
        self.store = store
        self.sender = sender

    def handle(self, message: IncomingMessage) -> DeliveryResult:
        processed = self.processor.process(message)
        if not processed.reply:
            return DeliveryResult(processed, DeliveryOutcome.NOT_SENT)
        if self.store.is_paused():
            return DeliveryResult(processed, DeliveryOutcome.SKIPPED_PAUSED)
        if not self.store.is_allowed(message.group_id):
            return DeliveryResult(processed, DeliveryOutcome.SKIPPED_NOT_ALLOWED)
        if not self.store.begin_delivery(message.message_id, message.group_id):
            return DeliveryResult(processed, DeliveryOutcome.NOT_SENT)
        try:
            receipt = self.sender.send_group_text(message.group_id, processed.reply)
        except GatewayFatalError:
            self.store.finish_delivery(message.message_id, confirmed=False)
            raise
        except Exception:
            self.store.finish_delivery(message.message_id, confirmed=False)
            return DeliveryResult(processed, DeliveryOutcome.UNCONFIRMED)
        self.store.finish_delivery(
            message.message_id, confirmed=receipt.confirmed,
            server_message_id=receipt.server_message_id,
        )
        delivery = (DeliveryOutcome.CONFIRMED if receipt.confirmed and receipt.server_message_id
                    else DeliveryOutcome.UNCONFIRMED)
        if delivery == DeliveryOutcome.CONFIRMED:
            self.processor.record_reply(message, processed.reply)
        return DeliveryResult(processed, delivery)
