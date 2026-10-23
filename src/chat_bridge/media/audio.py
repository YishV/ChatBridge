"""Bounded local audio decoding and PCM mixing; never opens network media."""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

from chat_bridge.media.kits import CoverError

RATE = 44100
MAX_BYTES = 12 * 1024 * 1024


def _pcm(path: Path) -> tuple[array, dict]:
    try:
        if not path.is_file() or not 44 <= path.stat().st_size <= MAX_BYTES:
            raise CoverError('audio_invalid')
        with wave.open(str(path), 'rb') as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (2, 2, RATE):
                raise CoverError('audio_invalid')
            frames = source.getnframes()
            if not 1 <= frames <= RATE * 30.5:
                raise CoverError('audio_invalid')
            data = source.readframes(frames)
            if len(data) != frames * 4:
                raise CoverError('audio_invalid')
        values = array('h', data)
        if sys.byteorder != 'little':
            values.byteswap()
        peak = max(abs(x) for x in values) / 32768
        rms = math.sqrt(sum(x * x for x in values) / len(values)) / 32768
        return values, {'duration': frames / RATE, 'peak': peak, 'rms': rms}
    except (OSError, EOFError, wave.Error, ValueError):
        raise CoverError('audio_invalid') from None


def inspect_wav(path: Path, *, allow_silence: bool = False) -> dict:
    _, info = _pcm(path)
    if not allow_silence and info['rms'] < 0.0001:
        raise CoverError('audio_silent')
    return info


def _publish(source: Path, destination: Path) -> None:
    try:
        # Both paths are on the same volume. Linking a fully written temporary
        # file is atomic and refuses to overwrite an existing destination.
        os.link(source, destination)
    except FileExistsError:
        raise CoverError('audio_output_exists') from None


def mix_wav(vocal: Path, backing: Path, destination: Path) -> dict:
    if destination.exists():
        raise CoverError('audio_output_exists')
    voice, v = _pcm(vocal)
    music, m = _pcm(backing)
    if v['rms'] < 0.0001:
        raise CoverError('audio_silent')
    if abs(v['duration'] - m['duration']) > 0.15:
        raise CoverError('audio_duration_mismatch')
    mixed = [x + y for x, y in zip(voice, music)]
    peak = max(abs(x) for x in mixed)
    gain = min(1.0, (32767 * 0.95) / max(1, peak))
    values = array('h', (round(x * gain) for x in mixed))
    if sys.byteorder != 'little':
        values.byteswap()
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.wav', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with wave.open(str(temporary), 'wb') as output:
            output.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
            output.writeframes(values.tobytes())
        info = inspect_wav(temporary)
        _publish(temporary, destination)
        return info
    finally:
        temporary.unlink(missing_ok=True)


class AudioTools:
    def __init__(self, executable: str):
        self.executable = executable

    @classmethod
    def discover(cls, executable: str = ''):
        if executable:
            found = shutil.which(executable)
            if not found:
                raise CoverError('ffmpeg_missing')
            return cls(found)
        found = shutil.which('ffmpeg')
        if found:
            return cls(found)
        try:
            import imageio_ffmpeg
            return cls(imageio_ffmpeg.get_ffmpeg_exe())
        except (ImportError, RuntimeError, OSError):
            raise CoverError('ffmpeg_missing') from None

    def decode(self, source: Path, destination: Path, *, start: float = 0,
               duration: float = 20) -> dict:
        if (type(start) not in (int, float) or type(duration) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(duration)
                or not 0 <= start <= 86400 or not 1 <= duration <= 30):
            raise CoverError('audio_time_invalid')
        if destination.exists():
            raise CoverError('audio_output_exists')
        if not source.is_file() or not 1 <= source.stat().st_size <= 100 * 1024 * 1024:
            raise CoverError('audio_input_invalid')
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.wav', delete=False) as handle:
            temporary = Path(handle.name)
        try:
            args = [self.executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,flac',
                    '-ss', str(start), '-i', str(source.resolve()), '-t', str(duration),
                    '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1',
                    '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s16le', '-fs', str(MAX_BYTES),
                    '-f', 'wav', str(temporary.resolve())]
            try:
                subprocess.run(args, check=True, timeout=60, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (subprocess.SubprocessError, OSError):
                raise CoverError('audio_decode_failed') from None
            info = inspect_wav(temporary)
            if abs(info['duration'] - duration) > 0.15:
                raise CoverError('audio_duration_mismatch')
            _publish(temporary, destination)
            return info
        finally:
            temporary.unlink(missing_ok=True)
