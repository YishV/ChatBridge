"""Admission, persistent event deduplication, and group conversation dispatch."""

from wechat_ai_bridge.message import IncomingMessage, Outcome, ProcessResult
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore


class MessageProcessor:
    def __init__(self, store: StateStore, chat: ChatService, *, cooldown_seconds: int = 5,
                 hourly_quota: int = 60, participation_mode: str = "natural",
                 spontaneous_cooldown_seconds: int = 30):
        if participation_mode not in {"natural", "mentions"}:
            raise ValueError("参与模式必须为 natural 或 mentions")
        if cooldown_seconds < 0 or spontaneous_cooldown_seconds < 0 or hourly_quota < 1:
            raise ValueError("冷却时间不能为负数，每小时配额必须为正数")
        self.store = store
        self.chat = chat
        self.cooldown_seconds = cooldown_seconds
        self.hourly_quota = hourly_quota
        self.participation_mode = participation_mode
        self.spontaneous_cooldown_seconds = max(30, spontaneous_cooldown_seconds)

    def process(self, message: IncomingMessage) -> ProcessResult:
        if (not message.is_group or message.is_from_bot or not message.text.strip()
                or not self.store.is_allowed(message.group_id) or self.store.is_paused()):
            return ProcessResult(Outcome.IGNORED)
        if not self.store.claim_event(message.message_id, message.group_id):
            return ProcessResult(Outcome.DUPLICATE)
        self.chat.observe(message)
        if not message.mentioned_bot and self.participation_mode == "mentions":
            return ProcessResult(Outcome.IGNORED)
        cooldown = self.cooldown_seconds if message.mentioned_bot else self.spontaneous_cooldown_seconds
        reservation = self.store.reserve(message.message_id, message.group_id, cooldown, self.hourly_quota)
        if reservation == "duplicate":
            return ProcessResult(Outcome.DUPLICATE)
        if reservation == "rate_limited":
            return ProcessResult(Outcome.RATE_LIMITED)
        try:
            answer = self.chat.respond(message)
        except (ModelError, ValueError, OSError):
            return ProcessResult(Outcome.ERROR)
        return ProcessResult(Outcome.ANSWERED if answer else Outcome.IGNORED, answer)

    def record_reply(self, message: IncomingMessage, text: str) -> None:
        self.chat.record_reply(message, text)
