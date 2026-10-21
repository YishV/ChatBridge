"""Configuration for the optional NapCat/OneBot 11 QQ gateway."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def _bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError(f'{name} must be true or false')
    return value in ('true', '1')


@dataclass(frozen=True)
class QQSettings:
    api_url: str = 'http://127.0.0.1:3000'
    access_token: str = field(default='', repr=False)
    webhook_host: str = '127.0.0.1'
    webhook_port: int = 8081
    webhook_path: str = '/onebot'
    webhook_secret: str = field(default='', repr=False)
    allowed_groups: frozenset[str] = frozenset()
    state_db: Path = Path('data/runtime/qq-chat-state.sqlite3')
    participation_mode: str = 'natural'
    cooldown_seconds: int = 5
    hourly_quota: int = 60
    spontaneous_cooldown_seconds: int = 5
    voice_enabled: bool = True
    gpt_sovits_url: str = 'http://127.0.0.1:9880'
    gpt_ref_audio: Path = Path('data/runtime/gpt-sovits/reference.wav')
    gpt_prompt_text: str = ''
    gpt_prompt_lang: str = 'zh'
    gpt_text_lang: str = 'zh'
    gpt_timeout_seconds: float = 30.0
    voice_max_chars: int = 120

    def __post_init__(self):
        parsed = urlparse(self.api_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('QQ API 地址无效')
        if (not isinstance(self.webhook_host, str) or not self.webhook_host.strip()
                or type(self.webhook_port) is not int or not 0 <= self.webhook_port <= 65535
                or not isinstance(self.webhook_path, str) or not self.webhook_path.startswith('/')):
            raise ValueError('QQ webhook 配置无效')
        if (self.participation_mode not in ('natural', 'mentions')
                or type(self.cooldown_seconds) is not int or not 0 <= self.cooldown_seconds <= 3600
                or type(self.hourly_quota) is not int or not 1 <= self.hourly_quota <= 1000
                or type(self.spontaneous_cooldown_seconds) is not int
                or not 5 <= self.spontaneous_cooldown_seconds <= 3600
                or type(self.voice_enabled) is not bool
                or type(self.voice_max_chars) is not int or not 1 <= self.voice_max_chars <= 500):
            raise ValueError('QQ 聊天配置无效')
        if not isinstance(self.gpt_prompt_text, str) or len(self.gpt_prompt_text) > 1000:
            raise ValueError('GPT-SoVITS prompt 配置无效')

    @classmethod
    def from_env(cls) -> 'QQSettings':
        load_dotenv(override=False, encoding='utf-8-sig')
        groups = frozenset(
            item.strip() for item in os.getenv('QQ_ALLOWED_GROUPS', '').split(',') if item.strip()
        )
        try:
            port = int(os.getenv('QQ_WEBHOOK_PORT', '8081'))
            cooldown = int(os.getenv('QQ_COOLDOWN_SECONDS', '5'))
            quota = int(os.getenv('QQ_HOURLY_QUOTA', '60'))
            spontaneous = int(os.getenv('QQ_SPONTANEOUS_COOLDOWN_SECONDS', '5'))
            gpt_timeout = float(os.getenv('QQ_GPT_SOVITS_TIMEOUT_SECONDS', '30'))
            max_chars = int(os.getenv('QQ_VOICE_MAX_CHARS', '120'))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('QQ 数值配置无效') from None
        return cls(
            api_url=os.getenv('QQ_ONEBOT_API_URL', 'http://127.0.0.1:3000').strip(),
            access_token=os.getenv('QQ_ONEBOT_TOKEN', '').strip(),
            webhook_host=os.getenv('QQ_WEBHOOK_HOST', '127.0.0.1').strip(),
            webhook_port=port,
            webhook_path=os.getenv('QQ_WEBHOOK_PATH', '/onebot').strip(),
            webhook_secret=os.getenv('QQ_WEBHOOK_SECRET', '').strip(),
            allowed_groups=groups,
            state_db=Path(os.getenv('QQ_STATE_DB', 'data/runtime/qq-chat-state.sqlite3')),
            participation_mode=os.getenv('QQ_PARTICIPATION_MODE', 'natural').strip(),
            cooldown_seconds=cooldown,
            hourly_quota=quota,
            spontaneous_cooldown_seconds=spontaneous,
            voice_enabled=_bool('QQ_VOICE_ENABLED', 'true'),
            gpt_sovits_url=os.getenv('QQ_GPT_SOVITS_URL', 'http://127.0.0.1:9880').strip(),
            gpt_ref_audio=Path(os.getenv('QQ_GPT_SOVITS_REF_AUDIO',
                                         'data/runtime/gpt-sovits/reference.wav')),
            gpt_prompt_text=os.getenv('QQ_GPT_SOVITS_PROMPT_TEXT', '').strip(),
            gpt_prompt_lang=os.getenv('QQ_GPT_SOVITS_PROMPT_LANG', 'zh').strip(),
            gpt_text_lang=os.getenv('QQ_GPT_SOVITS_TEXT_LANG', 'zh').strip(),
            gpt_timeout_seconds=gpt_timeout,
            voice_max_chars=max_chars,
        )
