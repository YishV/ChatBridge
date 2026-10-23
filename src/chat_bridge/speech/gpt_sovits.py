"""Bounded client for a local GPT-SoVITS api_v2 /tts service."""

import hashlib
import ipaddress
import json
import os
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from chat_bridge.speech.errors import SpeechError

_MAX_AUDIO_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class GPTSoVITSSettings:
    base_url: str = 'http://127.0.0.1:9880'
    ref_audio_path: Path = Path('data/voices/example/reference.wav')
    prompt_text: str = ''
    prompt_lang: str = 'zh'
    text_lang: str = 'zh'
    timeout_seconds: float = 30.0
    max_chars: int = 200

    def __post_init__(self):
        if (not isinstance(self.base_url, str) or not self.base_url
                or isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not 1 <= self.timeout_seconds <= 180
                or type(self.max_chars) is not int or not 1 <= self.max_chars <= 500
                or not isinstance(self.prompt_lang, str) or not 2 <= len(self.prompt_lang) <= 8
                or not isinstance(self.text_lang, str) or not 2 <= len(self.text_lang) <= 8):
            raise ValueError('gpt_sovits_settings_invalid')
        if not isinstance(self.prompt_text, str) or len(self.prompt_text) > 1000:
            raise ValueError('gpt_sovits_settings_invalid')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class GPTSoVITSClient:
    def __init__(self, settings: GPTSoVITSSettings):
        parsed = urlparse(settings.base_url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
            raise ValueError('gpt_sovits_url_invalid')
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost'
        if not loopback:
            raise ValueError('gpt_sovits_url_must_be_loopback')
        self.settings = settings
        self.url = settings.base_url.rstrip('/') + '/tts'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def synthesize(self, text: str, output: Path) -> Path:
        if not isinstance(text, str) or not text.strip():
            raise SpeechError('gpt_sovits_text_empty')
        text = text.strip()
        if len(text) > self.settings.max_chars:
            raise SpeechError('gpt_sovits_text_too_long')
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file() and output.stat().st_size > 0:
            return output
        reference = Path(self.settings.ref_audio_path).resolve()
        if not reference.is_file():
            raise SpeechError('gpt_sovits_reference_missing')
        payload = {
            'text': text,
            'text_lang': self.settings.text_lang,
            'ref_audio_path': str(reference),
            'prompt_text': self.settings.prompt_text,
            'prompt_lang': self.settings.prompt_lang,
            'text_split_method': 'cut5',
            'batch_size': 1,
            'media_type': 'wav',
            'streaming_mode': False,
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Accept': 'audio/wav'},
            method='POST',
        )
        temporary = output.with_suffix('.part.wav')
        try:
            with self.opener.open(request, timeout=self.settings.timeout_seconds) as response:
                status = getattr(response, 'status', 200)
                if status != 200:
                    raise SpeechError('gpt_sovits_http_error')
                raw = response.read(_MAX_AUDIO_BYTES + 1)
        except SpeechError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SpeechError('gpt_sovits_unavailable') from exc
        if len(raw) > _MAX_AUDIO_BYTES:
            raise SpeechError('gpt_sovits_audio_too_large')
        if not raw:
            raise SpeechError('gpt_sovits_empty_audio')
        try:
            temporary.write_bytes(raw)
            self._validate_wav(temporary)
            os.replace(temporary, output)
        except SpeechError:
            raise
        except (OSError, EOFError, wave.Error) as exc:
            raise SpeechError('gpt_sovits_invalid_audio') from exc
        finally:
            temporary.unlink(missing_ok=True)
        return output

    @staticmethod
    def _validate_wav(path: Path) -> None:
        with wave.open(str(path), 'rb') as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise SpeechError('gpt_sovits_invalid_audio')
            rate = audio.getframerate()
            frames = audio.getnframes()
            if not 8000 <= rate <= 96000 or frames <= 0:
                raise SpeechError('gpt_sovits_invalid_audio')
            duration = frames / rate
            if not 0.2 <= duration <= 60:
                raise SpeechError('gpt_sovits_invalid_audio')


def cache_name(text: str, settings: GPTSoVITSSettings) -> str:
    """Stable cache filename without putting spoken text on disk as a name."""
    digest = hashlib.sha256(
        (settings.text_lang + '\0' + settings.prompt_lang + '\0' + settings.prompt_text + '\0' + text).encode('utf-8')
    ).hexdigest()
    return digest + '.wav'
