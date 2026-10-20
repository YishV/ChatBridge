"""Local configuration for general group chat and its Windows gateway."""

import os
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
    spontaneous_cooldown_seconds: int = 30
    model_thinking: str = "disabled"
    gateway_config: Path = Path("data/runtime/native-hook/gateway.json")
    poll_seconds: float = 2.0
    system_prompt: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False, encoding="utf-8-sig")
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
            spontaneous_cooldown_seconds=int(os.getenv("WECHAT_AI_BRIDGE_SPONTANEOUS_COOLDOWN_SECONDS", "30")),
            model_thinking=os.getenv("WECHAT_AI_BRIDGE_MODEL_THINKING", "disabled"),
            gateway_config=Path(os.getenv("WECHAT_AI_BRIDGE_GATEWAY_CONFIG", "data/runtime/native-hook/gateway.json")),
            poll_seconds=float(os.getenv("WECHAT_AI_BRIDGE_POLL_SECONDS", "2")),
            system_prompt=os.getenv("WECHAT_AI_BRIDGE_SYSTEM_PROMPT", ""),
        )
        if (result.participation_mode not in ("natural", "mentions")
                or result.model_thinking not in ("enabled", "disabled")
                or not 1 <= result.poll_seconds <= 60
                or not 0 <= result.cooldown_seconds <= 3600
                or not 30 <= result.spontaneous_cooldown_seconds <= 3600
                or not 1 <= result.hourly_quota <= 1000):
            raise ValueError("聊天频率或参与方式配置无效")
        return result
