"""Coordinate TTS, VB-Cable, WeChat UI, and database receipt confirmation."""

import time
from dataclasses import dataclass
from pathlib import Path

from chat_bridge.voice.audio import CableAudio
from chat_bridge.voice.errors import VoiceError
from chat_bridge.voice.tts import EdgeTts
from chat_bridge.voice.wechat_ui import WeChatUi


@dataclass(frozen=True)
class VoiceSendResult:
    confirmed: bool
    server_message_id: str | None = None


class VoiceSender:
    def __init__(self, reader, pid: int, *, voice: str = 'zh-CN-XiaoxiaoNeural',
                 max_chars: int = 120, cache_dir: Path = Path('data/runtime/voice-cache'),
                 receipt_timeout: float = 15.0, tts=None, audio=None, ui=None):
        if type(max_chars) is not int or not 1 <= max_chars <= 500:
            raise VoiceError('voice_text_too_long')
        self.reader = reader
        self.max_chars = max_chars
        self.receipt_timeout = float(receipt_timeout)
        self.tts = tts if tts is not None else EdgeTts(voice, Path(cache_dir))
        self.audio = audio if audio is not None else CableAudio()
        self.ui = ui if ui is not None else WeChatUi(pid)

    def send_group_voice(self, group_id: str, text: str) -> VoiceSendResult:
        if not isinstance(text, str) or not text.strip():
            raise VoiceError('voice_text_empty')
        text = text.strip()
        if len(text) > self.max_chars:
            raise VoiceError('voice_text_too_long')
        chat_name = self.reader.conversation_display_name(group_id)
        if not chat_name:
            raise VoiceError('voice_chat_unknown')
        before = self.reader.snapshot(group_id)
        wav = self.tts.synthesize(text)
        started = False
        try:
            self.ui.begin()
            try:
                self.ui.open_chat(chat_name, prefer_group=True)
                self.ui.focus_input()
                with self.audio.microphone():
                    self.ui.start_recording()
                    started = True
                    time.sleep(0.4)
                    self.audio.play(wav)
                    time.sleep(0.4)
                    self.ui.stop_recording()
            finally:
                self.ui.end()
        except VoiceError as exc:
            if started:
                raise VoiceError(exc.code, sent=True) from exc
            raise
        except Exception as exc:
            if started:
                raise VoiceError('voice_ui_failed', sent=True) from exc
            raise VoiceError('voice_ui_failed') from exc
        return self._wait_receipt(group_id, before)

    def _wait_receipt(self, group_id: str, before) -> VoiceSendResult:
        deadline = time.monotonic() + self.receipt_timeout
        while True:
            try:
                rows = self.reader.read_after(group_id, before)
            except Exception:
                rows = []
            for row in rows:
                if (row.local_type == 34 and row.sender_id == self.reader.account_id
                        and row.server_id > 0):
                    return VoiceSendResult(True, str(row.server_id))
            if time.monotonic() >= deadline:
                return VoiceSendResult(False)
            time.sleep(0.5)
