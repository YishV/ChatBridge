"""Serial background worker for private-chat memory extraction."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.memory.extractor import apply_memory_action, summarize_turns
from wechat_ai_bridge.memory.store import MemoryStore


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryTask:
    message: IncomingMessage
    reply: str
    recent: tuple[tuple[str, str], ...]
    turn_number: int


class MemoryWorker:
    """One bounded queue and one daemon thread.

    Model calls for extraction and summarisation run serially so a busy private
    chat cannot create one thread per turn or burst past provider rate limits.
    """

    def __init__(self, model, store: MemoryStore, platform: str, *,
                 update_every: int = 3, summary_every: int = 30,
                 queue_size: int = 64):
        if type(update_every) is not int or update_every < 1:
            raise ValueError('memory_update_every_invalid')
        if type(summary_every) is not int or summary_every < 0:
            raise ValueError('memory_summary_every_invalid')
        if type(queue_size) is not int or not 1 <= queue_size <= 1000:
            raise ValueError('memory_queue_size_invalid')
        self.model = model
        self.store = store
        self.platform = platform
        self.update_every = update_every
        self.summary_every = summary_every
        self._queue: queue.Queue[MemoryTask] = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(
            target=self._loop, name='memory-worker', daemon=True
        )
        self._thread.start()

    def submit(self, task: MemoryTask) -> bool:
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            log.warning('memory queue full; dropping turn %s', task.message.message_id)
            return False

    def drain(self, timeout: float = 5.0) -> None:
        """Wait for currently queued tasks; useful for tests and shutdown."""
        self._queue.join()

    def _loop(self) -> None:
        while True:
            task = self._queue.get()
            try:
                self._process(task)
            except Exception:
                log.exception('memory task failed: %s', task.message.message_id)
            finally:
                self._queue.task_done()

    def _process(self, task: MemoryTask) -> None:
        key = task.message.group_id
        if task.turn_number % self.update_every == 0:
            apply_memory_action(
                self.model, self.store, self.platform, key,
                task.message.text, task.reply, task.message.message_id,
            )
        if self.summary_every and task.turn_number % self.summary_every == 0:
            summary = summarize_turns(
                self.model,
                self.store.get_summary(self.platform, key),
                list(task.recent),
            )
            if summary:
                self.store.save_summary(
                    self.platform, key, summary, task.turn_number
                )
