"""Transient console lines for messages heard and replies sent.

Lines go to stderr so the JSON status written to stdout stays machine-readable.
Message text is shown for local observation only; nothing here writes to a log
file, so content stays as transient as the terminal itself.
"""

import sys
import time

from wechat_ai_bridge.message import IncomingMessage, Outcome, ProcessResult

_MAX_TEXT = 500


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _clean(value: object) -> str:
    """Keep one log line per event and bound its length."""
    text = " ".join(str(value).splitlines())
    text = "".join(character for character in text if character.isprintable())
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _short(message_id: str) -> str:
    # Native message IDs are 64-hex digests; the tail is enough to correlate a
    # received line with its delivery line without flooding the console.
    return message_id if len(message_id) <= 24 else "…" + message_id[-8:]


def _reason(result: ProcessResult) -> str:
    if result.outcome == Outcome.ERROR:
        return f"模型错误({result.reason})" if result.reason else "模型错误"
    if result.outcome == Outcome.DUPLICATE:
        return "重复消息"
    if result.outcome == Outcome.RATE_LIMITED:
        return "限流或配额已满"
    if result.outcome == Outcome.DEFERRED:
        return "等待合并判断"
    if result.outcome == Outcome.IGNORED:
        return "模型选择沉默" if result.reason == "model_silent" else "忽略"
    return "未产生回复"


class ConsoleLog:
    """One line per heard message and per delivery decision."""

    def __init__(self, stream=None, *, timestamp=None):
        self._stream = stream
        self._timestamp = timestamp or _timestamp

    def _write(self, label: str, message: str | None = None, **fields) -> None:
        stream = sys.stderr if self._stream is None else self._stream
        if stream is None:
            return
        parts = [self._timestamp(), f"[{label}]"]
        if message is not None:
            parts.append(_clean(message))
        parts.extend(f"{name}={_clean(value)}"
                     for name, value in fields.items() if value is not None)
        try:
            print(" ".join(parts), file=stream, flush=True)
        except (OSError, ValueError):
            # Observability must never interrupt message handling.
            pass

    def notice(self, text: str) -> None:
        self._write("网关", text)

    def received(self, message: IncomingMessage) -> None:
        kind = "拍一拍" if message.interaction == "pat" else (
            "@" if message.mentioned_bot else "普通")
        self._write("收到", group=message.group_id, sender=message.sender_id,
                    kind=kind, id=_short(message.message_id), text=message.text)

    def image_received(self, group_id: str, sender_id: str) -> None:
        self._write('收到图片', '仅登记消息引用，等待发图者明确 @ 看图', group=group_id, sender=sender_id)

    def sent(self, message: IncomingMessage, reply: str, *, confirmed: bool,
             server_message_id: str | None = None, elapsed: float = 0.0) -> None:
        self._write("发送成功" if confirmed else "发送未确认",
                    group=message.group_id, id=_short(message.message_id),
                    elapsed=f"{elapsed:.1f}s", serverId=server_message_id,
                    text=reply)

    def not_sent(self, message: IncomingMessage, reply: str, reason: str) -> None:
        self._write("未发送", group=message.group_id, id=_short(message.message_id),
                    reason=reason, text=reply)

    def not_replied(self, message: IncomingMessage, result: ProcessResult) -> None:
        self._write("未回复", group=message.group_id, id=_short(message.message_id),
                    reason=_reason(result))


class NullConsole:
    """Silent collaborator so tests and one-off commands stay quiet."""

    def notice(self, text: str) -> None:
        pass

    def received(self, message: IncomingMessage) -> None:
        pass

    def image_received(self, group_id: str, sender_id: str) -> None:
        pass

    def sent(self, message: IncomingMessage, reply: str, *, confirmed: bool,
             server_message_id: str | None = None, elapsed: float = 0.0) -> None:
        pass

    def not_sent(self, message: IncomingMessage, reply: str, reason: str) -> None:
        pass

    def not_replied(self, message: IncomingMessage, result: ProcessResult) -> None:
        pass
