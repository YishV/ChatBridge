"""Subprocess orchestration for Demucs + RVC cover rendering."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from chat_bridge.singing.config import SingingSettings


class RenderError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CoverRenderer:
    def __init__(self, settings: SingingSettings, *, runner=subprocess.run):
        self.settings = settings
        self._run = runner

    def render(self, source: Path, output: Path) -> Path:
        source = Path(source).resolve()
        output = Path(output)
        if not source.is_file() or source.suffix.lower() not in {
                '.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'}:
            raise RenderError('source_invalid')
        if not self.settings.ffmpeg.is_file():
            raise RenderError('ffmpeg_missing')
        work = self.settings.rvc_home / '.work' / hashlib.sha256(
            str(source).encode()).hexdigest()[:16]
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        vocals, no_vocals = self._separate(source, work)
        converted = self._convert(vocals, work)
        mixed = work / 'mixed.mp3'
        self._mix(converted, no_vocals, mixed)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mixed, output)
        return output

    def _media_env(self):
        env = os.environ.copy()
        env['TORCH_HOME'] = str(self.settings.torch_home)
        env['PATH'] = str(self.settings.ffmpeg.parent) + os.pathsep + env.get('PATH', '')
        return env

    def _run_checked(self, command, cwd=None, env=None):
        try:
            self._run(command, cwd=str(cwd or self.settings.rvc_home), env=env, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RenderError('render_subprocess_failed') from exc

    def _separate(self, source: Path, work: Path):
        out = work / 'separated'
        self._run_checked([
            str(self.settings.rvc_python), '-m', 'demucs', '-n', 'htdemucs',
            '--two-stems=vocals', '-d', 'cuda', '-o', str(out), str(source),
        ], env=self._media_env())
        base = out / 'htdemucs' / source.stem
        vocals, no_vocals = base / 'vocals.wav', base / 'no_vocals.wav'
        if not vocals.is_file() or not no_vocals.is_file():
            raise RenderError('separation_failed')
        return vocals, no_vocals

    def _convert(self, vocals: Path, work: Path) -> Path:
        chunks = work / 'chunks'
        converted = work / 'converted'
        chunks.mkdir(exist_ok=True)
        converted.mkdir(exist_ok=True)
        model = self.settings.rvc_model
        model_path = Path(model)
        if not model_path.is_absolute():
            project_path = Path.cwd() / model_path
            model_path = project_path if project_path.is_file() else self.settings.rvc_home / model_path
        for start in range(0, 600, 30):
            chunk = chunks / f'v{start // 30}.wav'
            self._run_checked([
                str(self.settings.ffmpeg), '-y', '-ss', str(start), '-t', '30',
                '-i', str(vocals), '-ac', '1', '-ar', '40000', str(chunk),
            ])
            if chunk.stat().st_size < 4096:
                break
            target = converted / chunk.name
            self._run_checked([
                str(self.settings.rvc_python),
                str(Path(__file__).parents[3] / 'scripts' / 'rvc_runner.py'),
                str(self.settings.rvc_home), '--model', str(model_path),
                '--input', str(chunk), '--output', str(target),
                '--pitch', str(self.settings.pitch), '--f0-method', 'pm',
                '--index-rate', '0', '--format', 'wav',
            ], env=self._media_env())
        parts = sorted(converted.glob('v*.wav'))
        if not parts:
            raise RenderError('rvc_failed')
        concat = work / 'concat.txt'
        concat.write_text('\n'.join(f"file '{part.name}'" for part in parts), encoding='utf-8')
        joined = work / 'vocals_full.wav'
        self._run_checked([
            str(self.settings.ffmpeg), '-y', '-f', 'concat', '-safe', '0',
            '-i', str(concat), '-c', 'copy', str(joined),
        ], cwd=converted)
        return joined

    def _mix(self, vocals: Path, no_vocals: Path, output: Path):
        self._run_checked([
            str(self.settings.ffmpeg), '-y', '-i', str(vocals), '-i', str(no_vocals),
            '-filter_complex',
            '[0:a]volume=1.0[a0];[1:a]volume=1.0[a1];'
            '[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[m]',
            '-map', '[m]', '-ar', '44100', '-ac', '2', '-b:a', '320k', str(output),
        ])
