"""Optional voice-message feature exports."""

import importlib.util

from chat_bridge.voice.errors import VoiceError
from chat_bridge.voice.sender import VoiceSendResult, VoiceSender


def create_voice_sender(settings, reader, pid: int) -> VoiceSender:
    missing = [name for name in ('edge_tts', 'sounddevice', 'soundfile', 'numpy', 'pycaw', 'pyperclip')
               if importlib.util.find_spec(name) is None]
    if missing:
        raise VoiceError('voice_dependency_missing')
    return VoiceSender(
        reader, pid,
        voice=settings.voice_name,
        max_chars=settings.voice_max_chars,
    )


__all__ = ['VoiceError', 'VoiceSendResult', 'VoiceSender', 'create_voice_sender']
