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

    @staticmethod
    def _private_user(conversation_id: str) -> str | None:
        return conversation_id[8:] if conversation_id.startswith('private:') else None

    def send_group_text(self, group_id: str, text: str) -> SendReceipt:
        try:
            private = self._private_user(group_id)
            message_id = (self.client.send_private_text(private, text) if private is not None
                          else self.client.send_group_text(group_id, text))
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, message_id)

    def send_group_image(self, group_id: str, path: Path) -> SendReceipt:
        try:
            private = self._private_user(group_id)
            message_id = (self.client.send_private_image(private, path) if private is not None
                          else self.client.send_group_image(group_id, path))
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, message_id)

    def send_face(self, conversation_id: str, face_id: str) -> SendReceipt:
        try:
            private = self._private_user(conversation_id)
            message_id = (self.client.send_private_face(private, face_id) if private is not None
                          else self.client.send_group_face(conversation_id, face_id))
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, message_id)

    def send_poke(self, conversation_id: str, user_id: str) -> SendReceipt:
        try:
            result = self.client.send_poke(conversation_id, user_id)
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, result)

    def send_group_file(self, conversation_id: str, path: Path,
                        name: str | None = None) -> SendReceipt:
        try:
            private = self._private_user(conversation_id)
            file_id = (self.client.upload_private_file(private, path, name)
                       if private is not None
                       else self.client.upload_group_file(conversation_id, path, name))
        except OneBotError:
            return SendReceipt(False)
        return SendReceipt(True, file_id)

    def send_voice_file(self, conversation_id: str, path: Path) -> VoiceSendResult:
        try:
            private = self._private_user(conversation_id)
            message_id = (self.client.send_private_record(private, path)
                          if private is not None
                          else self.client.send_group_record(conversation_id, path))
        except OneBotError as exc:
            raise VoiceError('qq_voice_unconfirmed', sent=True) from exc
        return VoiceSendResult(True, message_id)

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
            private = self._private_user(group_id)
            message_id = (self.client.send_private_record(private, wav) if private is not None
                          else self.client.send_group_record(group_id, wav))
        except OneBotError as exc:
            # A transport error may have happened after NapCat queued the file.
            raise VoiceError('qq_voice_unconfirmed', sent=True) from exc
        return VoiceSendResult(True, message_id)
