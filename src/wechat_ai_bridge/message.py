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
    interaction: str = "text"
    sender_name: str | None = None

    @property
    def is_directed(self) -> bool:
        return not self.is_group or self.mentioned_bot or self.interaction == "pat"


class Outcome(StrEnum):
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    DEFERRED = "deferred"
    ANSWERED = "answered"
    UNCERTAIN = "uncertain"
    ERROR = "error"


@dataclass(frozen=True)
class ProcessResult:
    outcome: Outcome
    reply: str | None = None
    reason: str | None = None
    model_attempts: int = 0
    meme_id: str | None = None
    model_retries: int | None = None
    voice: bool = False


@dataclass(frozen=True)
class ChatReply:
    reply: str | None = None
    meme_id: str | None = None
    voice: bool = False
