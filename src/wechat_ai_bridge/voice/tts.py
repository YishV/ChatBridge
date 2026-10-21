"""Free Edge TTS synthesis for short group replies."""

import asyncio
import hashlib
import os
from pathlib import Path

from wechat_ai_bridge.voice.errors import VoiceError


class EdgeTts:
    def __init__(self, voice: str, cache_dir: Path, *, timeout: float = 30.0):
        if not isinstance(voice, str) or not 3 <= len(voice) <= 100:
            raise VoiceError('voice_name_invalid')
        if isinstance(timeout, bool) or not 1 <= timeout <= 120:
            raise VoiceError('voice_timeout_invalid')
        self.voice = voice
        self.cache_dir = Path(cache_dir)
        self.timeout = float(timeout)

    def synthesize(self, text: str) -> Path:
        if not isinstance(text, str) or not text.strip():
            raise VoiceError('voice_text_empty')
        text = text.strip()
        if len(text) > 500:
            raise VoiceError('voice_text_too_long')
        try:
            import edge_tts
        except ImportError:
            raise VoiceError('voice_tts_missing') from None
        digest = hashlib.sha256((self.voice + '\0' + text).encode('utf-8')).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / (digest + '.mp3')
        if path.is_file() and path.stat().st_size > 0:
            return path
        temporary = path.with_suffix('.tmp')
        try:
            async def save():
                communicate = edge_tts.Communicate(text, self.voice)
                await communicate.save(str(temporary))
            asyncio.run(asyncio.wait_for(save(), timeout=self.timeout))
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise VoiceError('voice_tts_empty')
            os.replace(temporary, path)
            return path
        except VoiceError:
            raise
        except (OSError, asyncio.TimeoutError, Exception) as exc:
            raise VoiceError('voice_tts_failed') from exc
        finally:
            temporary.unlink(missing_ok=True)
