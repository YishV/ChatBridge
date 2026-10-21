"""QQ OneBot sender: text, local GPT-SoVITS speech, and record segments."""

from pathlib import Path

from wechat_ai_bridge.qq.config import QQSettings
from wechat_ai_bridge.qq.onebot import OneBotClient, OneBotError
from wechat_ai_bridge.speech.errors import SpeechError
from wechat_ai_bridge.speech.gpt_sovits import GPTSoVITSClient, GPTSoVITSSettings, cache_name
from wechat_ai_bridge.voice.errors import VoiceError
from wechat_ai_bridge.voice.sender import VoiceSendResult
from wechat_ai_bridge.wechat.delivery import SendReceipt


class QQSender:
    def __init__(self, settings: QQSettings, client: OneBotClient | None = None,
                 speech: GPTSoVITSClient | None = None):
        self.settings = settings
        self.client = client if client is not None else OneBotClient(settings)
        self.cache_dir = self.settings.state_db.parent / 'qq-voice'
        self.speech = speech
        if self.speech is None and settings.voice_enabled:
            self.speech = GPTSoVITSClient(GPTSoVITSSettings(
                base_url=settings.gpt_sovits_url,
                ref_audio_path=settings.gpt_ref_audio,
                prompt_text=settings.gpt_prompt_text,
                prompt_lang=settings.gpt_prompt_lang,
                text_lang=settings.gpt_text_lang,
                timeout_seconds=settings.gpt_timeout_seconds,
                max_chars=settings.voice_max_chars,
            ))

    def send_group_text(self, group_id: str, text: str) -> SendReceipt:
        try:
            message_id = self.client.send_group_text(group_id, text)
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, message_id)

    def send_group_image(self, group_id: str, path: Path) -> SendReceipt:
        return SendReceipt(False)

    def send_group_voice(self, group_id: str, text: str) -> VoiceSendResult:
        if not self.settings.voice_enabled or self.speech is None:
            raise VoiceError('qq_voice_disabled')
        if not isinstance(text, str) or not text.strip():
            raise VoiceError('qq_voice_text_empty')
        text = text.strip()
        if len(text) > self.settings.voice_max_chars:
            raise VoiceError('qq_voice_text_too_long')
        output = self.cache_dir / cache_name(text, self.speech.settings)
        try:
            wav = self.speech.synthesize(text, output)
        except SpeechError as exc:
            raise VoiceError(exc.code) from exc
        try:
            message_id = self.client.send_group_record(group_id, wav)
        except OneBotError as exc:
            # A transport error may have happened after NapCat queued the file.
            raise VoiceError('qq_voice_unconfirmed', sent=True) from exc
        return VoiceSendResult(True, message_id)
