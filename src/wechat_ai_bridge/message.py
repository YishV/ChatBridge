"""Transport-neutral message and processing result contracts."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    group_id: str
    sender_id: str
    text: str
    mentioned_bot: bool
    is_group: bool = True
    is_from_bot: bool = False


class Outcome(StrEnum):
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    ANSWERED = "answered"
    UNCERTAIN = "uncertain"
    ERROR = "error"


@dataclass(frozen=True)
class ProcessResult:
    outcome: Outcome
    reply: str | None = None
