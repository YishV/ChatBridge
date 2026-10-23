"""One-shot delivery boundary for a future Windows WeChat adapter."""

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from pathlib import Path

from chat_bridge.console_log import NullConsole
from chat_bridge.dispatch.processor import MessageProcessor
from chat_bridge.message import IncomingMessage, ProcessResult
from chat_bridge.state.store import StateStore
from chat_bridge.voice.errors import VoiceError
from chat_bridge.wechat.errors import GatewayFatalError


@dataclass(frozen=True)
class SendReceipt:
    confirmed: bool
    server_message_id: str | None = None


class GroupSender(Protocol):
    def send_group_text(self, group_id: str, text: str) -> SendReceipt: ...

    def send_group_image(self, group_id: str, path: Path) -> SendReceipt: ...


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
    media_delivery: DeliveryOutcome = DeliveryOutcome.NOT_SENT
    media_reason: str | None = None


class DeliveryService:
    def __init__(self, processor: MessageProcessor, store: StateStore, sender: GroupSender,
                 *, console=None, meme_catalog=None, meme_cooldown_seconds: int = 60,
                 meme_recent_count: int = 5, voice_sender=None, voice_policy: str = 'directed'):
        if voice_policy not in ('directed', 'model'):
            raise ValueError('voice_policy_invalid')
        self.processor = processor
        self.store = store
        self.sender = sender
        self.console = NullConsole() if console is None else console
        self.meme_catalog = meme_catalog
        self.meme_cooldown_seconds = meme_cooldown_seconds
        self.meme_recent_count = meme_recent_count
        self.voice_sender = voice_sender
        self.voice_policy = voice_policy

    def handle(self, message: IncomingMessage) -> DeliveryResult:
        # A reply already logs itself as `发送`; its group echo would only add noise.
        if not message.is_from_bot:
            self.console.received(message)
        return self._deliver(message, self.processor.process(message))

    def flush_pending(self) -> list[DeliveryResult]:
        return [self._deliver(message, processed)
                for message, processed in self.processor.iter_pending()]

    def _deliver(self, message: IncomingMessage, processed: ProcessResult) -> DeliveryResult:
        if not processed.reply:
            if not message.is_from_bot:
                self.console.not_replied(message, processed)
            return DeliveryResult(processed, DeliveryOutcome.NOT_SENT)
        if self.store.is_paused():
            self.console.not_sent(message, processed.reply, "已暂停")
            return DeliveryResult(processed, DeliveryOutcome.SKIPPED_PAUSED)
        if not self.store.is_allowed(message.group_id):
            self.console.not_sent(message, processed.reply, "不在白名单")
            return DeliveryResult(processed, DeliveryOutcome.SKIPPED_NOT_ALLOWED)
        if not self.store.begin_delivery(message.message_id, message.group_id):
            self.console.not_sent(message, processed.reply, "该消息已投递过")
            return DeliveryResult(processed, DeliveryOutcome.NOT_SENT)
        if self.voice_sender is not None and self._wants_voice(message, processed):
            return self._deliver_voice(message, processed)
        return self._deliver_text(message, processed)

    def _wants_voice(self, message: IncomingMessage, processed: ProcessResult) -> bool:
        if self.voice_policy == 'model':
            return bool(processed.voice)
        return message.is_group and message.is_directed

    def _deliver_voice(self, message: IncomingMessage, processed: ProcessResult) -> DeliveryResult:
        started = time.monotonic()
        try:
            result = self.voice_sender.send_group_voice(message.group_id, processed.reply)
        except VoiceError as error:
            if error.sent:
                self.store.finish_delivery(message.message_id, confirmed=False)
                self.console.not_sent(message, processed.reply, "语音发送状态未知，不自动重发")
                return DeliveryResult(processed, DeliveryOutcome.UNCONFIRMED)
            self.console.notice("语音准备失败，改发文字回复")
            return self._deliver_text(message, processed)
        except Exception:
            self.store.finish_delivery(message.message_id, confirmed=False)
            self.console.not_sent(message, processed.reply, "语音发送失败，状态未知")
            return DeliveryResult(processed, DeliveryOutcome.UNCONFIRMED)
        elapsed = time.monotonic() - started
        history = self.processor.chat.history_entry(message, reply=processed.reply)
        self.store.finish_delivery(
            message.message_id, confirmed=result.confirmed,
            server_message_id=result.server_message_id,
            **({'chat': history} if history is not None else {}),
        )
        delivery = (DeliveryOutcome.CONFIRMED if result.confirmed and result.server_message_id
                    else DeliveryOutcome.UNCONFIRMED)
        self.console.sent(message, f"[语音] {processed.reply}",
                          confirmed=delivery == DeliveryOutcome.CONFIRMED,
                          server_message_id=result.server_message_id, elapsed=elapsed)
        if delivery == DeliveryOutcome.CONFIRMED:
            self.processor.record_reply(message, processed.reply)
            return self._deliver_extras(message, processed, DeliveryResult(processed, delivery))
        return DeliveryResult(processed, delivery)

    def _deliver_text(self, message: IncomingMessage, processed: ProcessResult) -> DeliveryResult:
        started = time.monotonic()
        try:
            receipt = self.sender.send_group_text(message.group_id, processed.reply)
        except GatewayFatalError as error:
            self.store.finish_delivery(message.message_id, confirmed=False)
            self.console.not_sent(message, processed.reply, f"致命发送错误({error})")
            raise
        except Exception as error:
            self.store.finish_delivery(message.message_id, confirmed=False)
            self.console.not_sent(message, processed.reply, f"发送失败({type(error).__name__})")
            return DeliveryResult(processed, DeliveryOutcome.UNCONFIRMED)
        elapsed = time.monotonic() - started
        history = self.processor.chat.history_entry(message, reply=processed.reply)
        self.store.finish_delivery(
            message.message_id, confirmed=receipt.confirmed,
            server_message_id=receipt.server_message_id,
            **({'chat': history} if history is not None else {}),
        )
        delivery = (DeliveryOutcome.CONFIRMED if receipt.confirmed and receipt.server_message_id
                    else DeliveryOutcome.UNCONFIRMED)
        self.console.sent(message, processed.reply, confirmed=delivery == DeliveryOutcome.CONFIRMED,
                          server_message_id=receipt.server_message_id, elapsed=elapsed)
        if delivery == DeliveryOutcome.CONFIRMED:
            self.processor.record_reply(message, processed.reply)
            return self._deliver_extras(message, processed,
                                        self._deliver_image(message, processed))
        return DeliveryResult(processed, delivery)

    def _deliver_extras(self, message: IncomingMessage, processed: ProcessResult,
                        result: DeliveryResult) -> DeliveryResult:
        if processed.face_id:
            sender = getattr(self.sender, 'send_face', None)
            if callable(sender):
                try:
                    receipt = sender(message.group_id, processed.face_id)
                    self.console.notice(
                        f'QQ 原生表情 #{processed.face_id}：'
                        + ('已发送' if receipt.confirmed else '发送未确认'))
                except Exception:
                    self.console.notice(f'QQ 原生表情 #{processed.face_id} 发送失败')
        if processed.poke:
            sender = getattr(self.sender, 'send_poke', None)
            if callable(sender):
                try:
                    receipt = sender(message.group_id, message.sender_id)
                    self.console.notice('戳一戳：' + ('已发送' if receipt.confirmed else '发送未确认'))
                except Exception:
                    self.console.notice('戳一戳发送失败')
        return result

    def _deliver_image(self, message: IncomingMessage, processed: ProcessResult) -> DeliveryResult:
        text_delivery = DeliveryOutcome.CONFIRMED
        if not processed.meme_id or self.meme_catalog is None:
            return DeliveryResult(processed, text_delivery)
        asset = self.meme_catalog.get(processed.meme_id)
        if asset is None:
            return DeliveryResult(processed, text_delivery, media_reason='meme_unknown_asset')
        if not self.store.media_available(message.group_id, cooldown_seconds=self.meme_cooldown_seconds):
            return DeliveryResult(processed, text_delivery, media_reason='meme_cooldown_or_policy')
        try:
            path = self.meme_catalog.prepare(asset.id)
        except (ValueError, OSError):
            self.console.notice('表情图准备失败，已保留文字回复')
            return DeliveryResult(processed, text_delivery, media_reason='meme_prepare_failed')
        if not self.store.begin_media_delivery(message.message_id, message.group_id, asset.id,
                cooldown_seconds=self.meme_cooldown_seconds, recent_limit=self.meme_recent_count):
            return DeliveryResult(processed, text_delivery, media_reason='meme_cooldown_or_policy')
        try:
            receipt = self.sender.send_group_image(message.group_id, path)
        except GatewayFatalError:
            self.store.finish_media_delivery(message.message_id, confirmed=False)
            raise
        except Exception:
            receipt = SendReceipt(False)
        confirmed = bool(receipt.confirmed and receipt.server_message_id)
        marker = f'[已发送表情包 #{asset.id}：{asset.description[:200]}]'
        history = self.processor.chat.history_entry(message, reply=marker)
        self.store.finish_media_delivery(message.message_id, confirmed=confirmed,
            server_message_id=receipt.server_message_id, chat=history)
        if confirmed:
            self.processor.record_reply(message, marker)
        self.console.notice(f'表情图 #{asset.id}：' + ('已确认发送' if confirmed else '发送未确认，不自动重发'))
        return DeliveryResult(processed, text_delivery,
            DeliveryOutcome.CONFIRMED if confirmed else DeliveryOutcome.UNCONFIRMED,
            None if confirmed else 'meme_send_unconfirmed')
