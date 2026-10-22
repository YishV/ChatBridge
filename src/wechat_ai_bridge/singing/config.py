"""Environment-backed settings for the optional singing feature."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError(f'{name} must be true or false')
    return value in ('true', '1')


@dataclass(frozen=True)
class SingingSettings:
    enabled: bool = False
    music_dir: Path = Path('data/music')
    send_mp3: bool = False
    voice_excerpt_seconds: int = 30
    max_duration_seconds: int = 600
    queue_db: Path = Path('data/runtime/singing.sqlite3')
    output_dir: Path = Path('data/runtime/covers')
    rvc_home: Path = Path('tools/RVC')
    rvc_python: Path = Path(
        'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe'
    )
    rvc_model: str = 'assets/weights/da_fei_yu_rvc.pth'
    ffmpeg: Path = Path(
        'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/ffmpeg.exe'
    )
    torch_home: Path = Path(
        'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/cache/torch'
    )
    pitch: int = 12
    max_queue: int = 50
    poll_seconds: float = 1.0

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.send_mp3) is not bool:
            raise ValueError('SINGING_ENABLED and SINGING_SEND_MP3 must be bool')
        if type(self.voice_excerpt_seconds) is not int or not 1 <= self.voice_excerpt_seconds <= 120:
            raise ValueError('SINGING_VOICE_EXCERPT_SECONDS must be 1..120')
        if type(self.max_duration_seconds) is not int or not 1 <= self.max_duration_seconds <= 3600:
            raise ValueError('SINGING_MAX_DURATION_SECONDS must be 1..3600')
        if type(self.pitch) is not int or not -24 <= self.pitch <= 24:
            raise ValueError('SINGING_PITCH must be -24..24')
        if type(self.max_queue) is not int or not 1 <= self.max_queue <= 500:
            raise ValueError('SINGING_MAX_QUEUE must be 1..500')
        if type(self.poll_seconds) not in (int, float) or not 0.1 <= self.poll_seconds <= 60:
            raise ValueError('SINGING_POLL_SECONDS must be 0.1..60')
        if not isinstance(self.rvc_model, str) or not self.rvc_model.strip():
            raise ValueError('SINGING_RVC_MODEL must not be empty')
        for name in ('music_dir', 'queue_db', 'output_dir', 'rvc_home', 'rvc_python',
                     'ffmpeg', 'torch_home'):
            value = Path(getattr(self, name)).expanduser().resolve()
            object.__setattr__(self, name, value)

    @classmethod
    def from_env(cls) -> 'SingingSettings':
        load_dotenv(override=False, encoding='utf-8-sig')
        try:
            excerpt = int(os.getenv('SINGING_VOICE_EXCERPT_SECONDS', '30'))
            duration = int(os.getenv('SINGING_MAX_DURATION_SECONDS', '600'))
            pitch = int(os.getenv('SINGING_PITCH', '12'))
            max_queue = int(os.getenv('SINGING_MAX_QUEUE', '50'))
            poll = float(os.getenv('SINGING_POLL_SECONDS', '1.0'))
        except (TypeError, ValueError, OverflowError):
            raise ValueError('SINGING 数值配置无效') from None
        return cls(
            enabled=_bool('SINGING_ENABLED', 'false'),
            music_dir=Path(os.getenv('SINGING_MUSIC_DIR', 'data/music')),
            send_mp3=_bool('SINGING_SEND_MP3', 'false'),
            voice_excerpt_seconds=excerpt,
            max_duration_seconds=duration,
            queue_db=Path(os.getenv('SINGING_QUEUE_DB', 'data/runtime/singing.sqlite3')),
            output_dir=Path(os.getenv('SINGING_OUTPUT_DIR', 'data/runtime/covers')),
            rvc_home=Path(os.getenv('SINGING_RVC_HOME', 'tools/RVC')),
            rvc_python=Path(os.getenv(
                'SINGING_RVC_PYTHON',
                'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/runtime/python.exe',
            )),
            rvc_model=os.getenv(
                'SINGING_RVC_MODEL', 'assets/weights/da_fei_yu_rvc.pth'
            ).strip(),
            ffmpeg=Path(os.getenv(
                'SINGING_FFMPEG',
                'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/ffmpeg.exe',
            )),
            torch_home=Path(os.getenv(
                'SINGING_TORCH_HOME',
                'tools/GPT-SoVITS-v2/GPT-SoVITS-v2-240821/cache/torch',
            )),
            pitch=pitch,
            max_queue=max_queue,
            poll_seconds=poll,
        )
