"""Local configuration for general group chat and its Windows gateway."""

import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    state_db: Path
    model_name: str
    model_base_url: str
    allowed_model_hosts: frozenset[str]
    model_api_key: str = field(repr=False)
    cooldown_seconds: int = 5
    hourly_quota: int = 60
    participation_mode: str = "natural"
    spontaneous_cooldown_seconds: int = 5
    model_thinking: str = "disabled"
    gateway_config: Path = Path("data/runtime/native-hook/gateway.json")
    poll_seconds: float = 2.0
    system_prompt: str = ""
    prompt_file: Path = Path("config/persona.md")
    model_timeout_seconds: float = 45
    model_max_tokens: int = 1200
    history_context_messages: int = 50
    memes_enabled: bool = True
    meme_cache_dir: Path = Path('data/runtime/memes')
    meme_cooldown_seconds: int = 60
    meme_recent_count: int = 5
    memory_enabled: bool = True
    memory_db: Path = Path('data/runtime/memory.sqlite3')
    voice_enabled: bool = False
    voice_name: str = "zh-CN-XiaoxiaoNeural"
    voice_max_chars: int = 120
    weather_enabled: bool = True
    weather_timeout_seconds: float = 5
    weather_cache_seconds: int = 600
    vision_enabled: bool = True
    vision_window_seconds: int = 120

    def __post_init__(self):
        if type(self.memory_enabled) is not bool:
            raise ValueError('长期记忆开关无效')
        if (type(self.vision_enabled) is not bool or type(self.vision_window_seconds) is not int
                or not 30 <= self.vision_window_seconds <= 300):
            raise ValueError('看图开关或关联时间窗口无效')
        if (type(self.weather_enabled) is not bool or isinstance(self.weather_timeout_seconds, bool)
                or not isinstance(self.weather_timeout_seconds, (int, float))
                or not math.isfinite(self.weather_timeout_seconds) or not 1 <= self.weather_timeout_seconds <= 30
                or type(self.weather_cache_seconds) is not int or not 0 <= self.weather_cache_seconds <= 3600):
            raise ValueError('天气开关、超时或缓存配置无效')
        if (type(self.memes_enabled) is not bool or type(self.meme_cooldown_seconds) is not int
                or not 0 <= self.meme_cooldown_seconds <= 3600
                or type(self.meme_recent_count) is not int or not 1 <= self.meme_recent_count <= 20):
            raise ValueError('表情包开关或频率配置无效')
        if type(self.history_context_messages) is not int or not 1 <= self.history_context_messages <= 200:
            raise ValueError('聊天恢复条数必须为 1 至 200')
        if (type(self.voice_enabled) is not bool
                or not isinstance(self.voice_name, str) or not 3 <= len(self.voice_name) <= 100
                or type(self.voice_max_chars) is not int or not 1 <= self.voice_max_chars <= 500):
            raise ValueError('语音配置无效')
        if (isinstance(self.model_timeout_seconds, bool)
                or not isinstance(self.model_timeout_seconds, (int, float))
                or not 1 <= self.model_timeout_seconds <= 120
                or not math.isfinite(self.model_timeout_seconds)):
            raise ValueError("模型请求超时必须为 1 至 120 秒")
        if (isinstance(self.model_max_tokens, bool) or not isinstance(self.model_max_tokens, int)
                or not 128 <= self.model_max_tokens <= 8192):
            raise ValueError("模型输出上限必须为 128 至 8192 个 token")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False, encoding="utf-8-sig")
        vision_enabled = os.getenv('WECHAT_AI_BRIDGE_VISION_ENABLED', 'true').strip().lower()
        if vision_enabled not in ('true', 'false', '1', '0'):
            raise ValueError('看图开关必须为 true 或 false')
        weather_enabled = os.getenv('WECHAT_AI_BRIDGE_WEATHER_ENABLED', 'true').strip().lower()
        if weather_enabled not in ('true', 'false', '1', '0'):
            raise ValueError('天气开关必须为 true 或 false')
        memes_enabled = os.getenv('WECHAT_AI_BRIDGE_MEMES_ENABLED', 'true').strip().lower()
        if memes_enabled not in ('true', 'false', '1', '0'):
            raise ValueError('表情包开关必须为 true 或 false')
        memory_enabled = os.getenv('WECHAT_AI_BRIDGE_MEMORY_ENABLED', 'true').strip().lower()
        if memory_enabled not in ('true', 'false', '1', '0'):
            raise ValueError('长期记忆开关必须为 true 或 false')
        voice_enabled = os.getenv('WECHAT_AI_BRIDGE_VOICE_ENABLED', 'false').strip().lower()
        if voice_enabled not in ('true', 'false', '1', '0'):
            raise ValueError('语音开关必须为 true 或 false')
        try:
            model_timeout_seconds = float(os.getenv("WECHAT_AI_BRIDGE_MODEL_TIMEOUT_SECONDS", "45"))
            model_max_tokens = int(os.getenv("WECHAT_AI_BRIDGE_MODEL_MAX_TOKENS", "1200"))
        except (ValueError, OverflowError):
            raise ValueError("模型请求超时或输出上限配置无效") from None
        result = cls(
            state_db=Path(os.getenv("WECHAT_AI_BRIDGE_STATE_DB", "data/runtime/chat-state.sqlite3")),
            model_name=os.getenv("WECHAT_AI_BRIDGE_MODEL_NAME", "deepseek-flash"),
            model_base_url=os.getenv("WECHAT_AI_BRIDGE_MODEL_BASE_URL", "https://api.deepseek.com"),
            allowed_model_hosts=frozenset(
                host.strip().lower()
                for host in os.getenv("WECHAT_AI_BRIDGE_ALLOWED_MODEL_HOSTS", "api.deepseek.com").split(",")
                if host.strip()
            ),
            model_api_key=os.getenv("WECHAT_AI_BRIDGE_MODEL_API_KEY", ""),
            cooldown_seconds=int(os.getenv("WECHAT_AI_BRIDGE_COOLDOWN_SECONDS", "5")),
            hourly_quota=int(os.getenv("WECHAT_AI_BRIDGE_HOURLY_QUOTA", "60")),
            participation_mode=os.getenv("WECHAT_AI_BRIDGE_PARTICIPATION_MODE", "natural"),
            spontaneous_cooldown_seconds=int(os.getenv("WECHAT_AI_BRIDGE_SPONTANEOUS_COOLDOWN_SECONDS", "5")),
            model_thinking=os.getenv("WECHAT_AI_BRIDGE_MODEL_THINKING", "disabled"),
            gateway_config=Path(os.getenv("WECHAT_AI_BRIDGE_GATEWAY_CONFIG", "data/runtime/native-hook/gateway.json")),
            poll_seconds=float(os.getenv("WECHAT_AI_BRIDGE_POLL_SECONDS", "2")),
            system_prompt=os.getenv("WECHAT_AI_BRIDGE_SYSTEM_PROMPT", ""),
            prompt_file=Path(os.getenv("WECHAT_AI_BRIDGE_PROMPT_FILE", "config/persona.md")),
            model_timeout_seconds=model_timeout_seconds,
            model_max_tokens=model_max_tokens,
            history_context_messages=int(os.getenv('WECHAT_AI_BRIDGE_HISTORY_CONTEXT_MESSAGES', '50')),
            memes_enabled=memes_enabled in ('true', '1'),
            meme_cache_dir=Path(os.getenv('WECHAT_AI_BRIDGE_MEME_CACHE_DIR', 'data/runtime/memes')),
            meme_cooldown_seconds=int(os.getenv('WECHAT_AI_BRIDGE_MEME_COOLDOWN_SECONDS', '60')),
            meme_recent_count=int(os.getenv('WECHAT_AI_BRIDGE_MEME_RECENT_COUNT', '5')),
            memory_enabled=memory_enabled in ('true', '1'),
            memory_db=Path(os.getenv('WECHAT_AI_BRIDGE_MEMORY_DB', 'data/runtime/memory.sqlite3')),
            voice_enabled=voice_enabled in ('true', '1'),
            voice_name=os.getenv('WECHAT_AI_BRIDGE_VOICE_NAME', 'zh-CN-XiaoxiaoNeural').strip(),
            voice_max_chars=int(os.getenv('WECHAT_AI_BRIDGE_VOICE_MAX_CHARS', '120')),
            weather_enabled=weather_enabled in ('true', '1'),
            weather_timeout_seconds=float(os.getenv('WECHAT_AI_BRIDGE_WEATHER_TIMEOUT_SECONDS', '5')),
            weather_cache_seconds=int(os.getenv('WECHAT_AI_BRIDGE_WEATHER_CACHE_SECONDS', '600')),
            vision_enabled=vision_enabled in ('true', '1'),
            vision_window_seconds=int(os.getenv('WECHAT_AI_BRIDGE_VISION_WINDOW_SECONDS', '120')),
        )
        if (result.participation_mode not in ("natural", "mentions")
                or result.model_thinking not in ("enabled", "disabled")
                or not 1 <= result.poll_seconds <= 60
                or not 0 <= result.cooldown_seconds <= 3600
                or not 5 <= result.spontaneous_cooldown_seconds <= 3600
                or not 1 <= result.hourly_quota <= 1000):
            raise ValueError("聊天频率或参与方式配置无效")
        return result
