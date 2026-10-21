"""Admission, persistent event deduplication, and group conversation dispatch."""

from collections.abc import Callable, Iterator
import hashlib
from time import monotonic, sleep

from wechat_ai_bridge.message import IncomingMessage, Outcome, ProcessResult
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore


class _CallBlocked(Exception):
    def __init__(self, outcome: Outcome, reason: str):
        self.outcome, self.reason = outcome, reason


class MessageProcessor:
    def __init__(self, store: StateStore, chat: ChatService, *, cooldown_seconds: int = 5,
                 hourly_quota: int = 60, participation_mode: str = "natural",
                 spontaneous_cooldown_seconds: int = 5, clock: Callable[[], float] = monotonic,
                 retry_sleep: Callable[[float], None] = sleep):
        if participation_mode not in {"natural", "mentions"}:
            raise ValueError("参与模式必须为 natural 或 mentions")
        if cooldown_seconds < 0 or spontaneous_cooldown_seconds < 0 or hourly_quota < 1:
            raise ValueError("冷却时间不能为负数，每小时配额必须为正数")
        self.store = store
        self.chat = chat
        self.cooldown_seconds = cooldown_seconds
        self.hourly_quota = hourly_quota
        self.participation_mode = participation_mode
        self.spontaneous_cooldown_seconds = max(5, spontaneous_cooldown_seconds)
        self.clock = clock
        self.retry_sleep = retry_sleep
        self._pending: dict[str, tuple[IncomingMessage, float]] = {}

    def process(self, message: IncomingMessage) -> ProcessResult:
        if (message.is_from_bot or not message.text.strip()
                or not self.store.is_allowed(message.group_id) or self.store.is_paused()):
            return ProcessResult(Outcome.IGNORED)
        history = self.chat.history_entry(message)
        claimed = (self.store.claim_event(message.message_id, message.group_id, chat=history)
                   if history is not None else self.store.claim_event(message.message_id, message.group_id))
        if not claimed:
            return ProcessResult(Outcome.DUPLICATE)
        self.chat.observe(message)
        if not message.is_directed:
            if self.participation_mode == "mentions":
                return ProcessResult(Outcome.IGNORED)
            self._pending[message.group_id] = (message, self.clock() + self.spontaneous_cooldown_seconds)
            return ProcessResult(Outcome.DEFERRED)
        self._pending.pop(message.group_id, None)
        return self._respond(message)

    def flush_pending(self) -> list[tuple[IncomingMessage, ProcessResult]]:
        return list(self.iter_pending())

    def iter_pending(self) -> Iterator[tuple[IncomingMessage, ProcessResult]]:
        now = self.clock()
        # Remove the entire ready batch before inference so failures cannot retry it.
        ready = []
        for group_id, (message, deadline) in list(self._pending.items()):
            if deadline <= now or self.store.is_paused() or not self.store.is_allowed(group_id):
                del self._pending[group_id]
                ready.append(message)
        for message in ready:
            if self.store.is_paused() or not self.store.is_allowed(message.group_id):
                result = ProcessResult(Outcome.IGNORED)
            else:
                result = self._respond(message)
            yield message, result

    def _respond(self, message: IncomingMessage) -> ProcessResult:
        cooldown = self.cooldown_seconds if message.is_directed else self.spontaneous_cooldown_seconds
        reservation = self.store.reserve(message.message_id, message.group_id, cooldown, self.hourly_quota)
        if reservation == "duplicate":
            return ProcessResult(Outcome.DUPLICATE)
        if reservation == "rate_limited":
            return ProcessResult(Outcome.RATE_LIMITED)
        calls, retries = 0, 0
        retrying = False

        def guard():
            if self.store.is_paused() or not self.store.is_allowed(message.group_id):
                raise _CallBlocked(Outcome.IGNORED, 'paused_or_not_allowed')

        def model_call(method, *args, **kwargs):
            nonlocal calls, retries
            guard()
            if calls:
                call_id = 'model-call:' + hashlib.sha256(f'{message.message_id}:{calls + 1}'.encode()).hexdigest()
                if self.store.reserve(call_id, message.group_id, 0, self.hourly_quota) != 'reserved':
                    raise _CallBlocked(Outcome.RATE_LIMITED, 'model_quota')
            calls += 1
            if retrying:
                retries += 1
            return method(*args, **kwargs)

        last_error = None
        for attempt in (1, 2):
            try:
                guard()
                generated = self.chat.generate(message, model_call=model_call, guard=guard)
                answer = generated.reply
            except _CallBlocked as blocked:
                if blocked.outcome == Outcome.RATE_LIMITED and last_error is not None:
                    return ProcessResult(Outcome.ERROR, reason=last_error.code, model_attempts=calls, model_retries=retries)
                return ProcessResult(blocked.outcome, reason=blocked.reason, model_attempts=calls, model_retries=retries)
            except ModelError as error:
                last_error = error
                self.store.record_model_failure(error.code, attempt)
                if attempt == 1 and error.retryable:
                    if self.store.is_paused() or not self.store.is_allowed(message.group_id):
                        return ProcessResult(Outcome.IGNORED, reason='paused_or_not_allowed', model_attempts=calls, model_retries=retries)
                    retrying = True
                    self.retry_sleep(0.5)
                    continue
                return ProcessResult(Outcome.ERROR, reason=error.code, model_attempts=calls, model_retries=retries)
            except (ValueError, OSError):
                return ProcessResult(Outcome.ERROR, reason='prompt_or_chat_configuration', model_attempts=calls, model_retries=retries)
            return ProcessResult(Outcome.ANSWERED if answer else Outcome.IGNORED, answer,
                                 reason=None if answer else 'model_silent', model_attempts=calls,
                                 meme_id=generated.meme_id, voice=generated.voice, model_retries=retries)

    def record_reply(self, message: IncomingMessage, text: str) -> None:
        self.chat.record_reply(message, text)
