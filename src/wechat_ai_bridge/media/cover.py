"""Explicit local cover samples, separate from chat and WeChat delivery."""

import argparse
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from wechat_ai_bridge.media.audio import AudioTools, inspect_wav, mix_wav
from wechat_ai_bridge.media.kits import CoverError, KitsClient
from wechat_ai_bridge.wechat.errors import GatewayError
from wechat_ai_bridge.wechat.instance import SingleInstance


@dataclass(frozen=True)
class CoverConfig:
    api_key: str = field(default='', repr=False)
    work_dir: Path = Path('data/runtime/cover')
    download_hosts: frozenset[str] = frozenset({
        'arpeggi-prod-public.s3.us-west-2.amazonaws.com',
        'arpeggi-prod-public.kitsstorage.com',
    })
    ffmpeg: str = ''
    female_voice_ids: frozenset[int] = frozenset({1014961})

    @classmethod
    def from_env(cls):
        load_dotenv('.env.cover', override=False, encoding='utf-8-sig')
        hosts = os.getenv('WECHAT_AI_BRIDGE_KITS_DOWNLOAD_HOSTS', '')
        kwargs = {'download_hosts': frozenset(x.strip().lower() for x in hosts.split(',') if x.strip())} if hosts else {}
        ids = os.getenv('WECHAT_AI_BRIDGE_KITS_FEMALE_VOICE_IDS', '1014961')
        try:
            female_ids = frozenset(int(x.strip()) for x in ids.split(',') if x.strip())
        except ValueError:
            raise CoverError('cover_voice_config_invalid') from None
        if not female_ids or any(not _positive(x) for x in female_ids):
            raise CoverError('cover_voice_config_invalid')
        return cls(api_key=os.getenv('WECHAT_AI_BRIDGE_KITS_API_KEY', ''),
                   ffmpeg=os.getenv('WECHAT_AI_BRIDGE_COVER_FFMPEG', ''), female_voice_ids=female_ids, **kwargs)


def _hash(path: Path) -> str:
    if not path.is_file() or not 1 <= path.stat().st_size <= 20 * 1024 * 1024:
        raise CoverError('cover_artifact_invalid')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive(value) -> bool:
    return type(value) is int and 0 < value < 2**63


class CoverSample:
    def __init__(self, config: CoverConfig, *, tools=None, client=None, sleep=time.sleep):
        self.config = config
        self.tools = tools
        self.client = client
        self.sleep = sleep

    def _directory(self, session: str) -> Path:
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session):
            raise CoverError('cover_session_invalid')
        root = self.config.work_dir.resolve()
        directory = root / session
        if directory.resolve().parent != root or directory.is_symlink():
            raise CoverError('cover_session_invalid')
        return directory

    def _save(self, directory: Path, manifest: dict) -> None:
        temporary = directory / 'manifest.tmp'
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(directory / 'manifest.json')

    def _load(self, directory: Path) -> dict:
        path = directory / 'manifest.json'
        try:
            if path.is_symlink() or path.stat().st_size > 32768:
                raise CoverError('cover_manifest_invalid')
            result = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(result, dict) or result.get('version') != 1
                    or not _positive(result.get('voice_id'))
                    or type(result.get('duration')) not in (int, float)
                    or not math.isfinite(result['duration']) or not 1 <= result['duration'] <= 30
                    or type(result.get('pitch_shift')) is not int or not -24 <= result['pitch_shift'] <= 24
                    or not isinstance(result.get('artifacts'), dict)
                    or not isinstance(result.get('clip_sha256'), str)):
                raise CoverError('cover_manifest_invalid')
            for stage in ('separation', 'conversion'):
                item = result.get(stage)
                if (not isinstance(item, dict) or item.get('state') not in ('new', 'submitting', 'submitted', 'skipped')
                        or stage == 'conversion' and item.get('state') == 'skipped'
                        or item.get('state') == 'submitted' and not _positive(item.get('job_id'))):
                    raise CoverError('cover_manifest_invalid')
            return result
        except (OSError, ValueError, TypeError, KeyError):
            raise CoverError('cover_manifest_invalid') from None

    def _audio(self):
        if self.tools is None:
            self.tools = AudioTools.discover(self.config.ffmpeg)
        return self.tools

    def _provider(self):
        if self.client is None:
            self.client = KitsClient(self.config.api_key, self.config.download_hosts)
        return self.client

    def prepare(self, source: Path | None, *, song: str, artist: str, start: float,
                duration: float, voice_id: int, session: str | None = None, pitch_shift: int = 0,
                vocal: Path | None = None, backing: Path | None = None) -> dict:
        if (not _positive(voice_id) or not isinstance(song, str) or not song.strip() or len(song) > 200
                or not isinstance(artist, str) or not artist.strip() or len(artist) > 200
                or type(pitch_shift) is not int or not -24 <= pitch_shift <= 24
                or type(start) not in (int, float) or type(duration) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(duration)
                or not 0 <= start <= 86400 or not 1 <= duration <= 30):
            raise CoverError('cover_input_invalid')
        if not ((source is not None and vocal is None and backing is None)
                or (source is None and vocal is not None and backing is not None)):
            raise CoverError('cover_input_invalid')
        session = session or uuid4().hex
        directory = self._directory(session)
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            raise CoverError('cover_session_exists') from None
        artifacts = {}
        if source is not None:
            info = self._audio().decode(source, directory / 'clip.wav', start=start, duration=duration)
        else:
            self._audio().decode(vocal, directory / 'vocal.wav', start=start, duration=duration)
            self._audio().decode(backing, directory / 'backing.wav', start=start, duration=duration)
            info = mix_wav(directory / 'vocal.wav', directory / 'backing.wav', directory / 'clip.wav')
            artifacts = {name: _hash(directory / (name + '.wav')) for name in ('vocal', 'backing')}
        manifest = {'version': 1, 'provider': 'kits', 'state': 'prepared',
                    'song': song.strip(), 'artist': artist.strip(), 'start': start, 'duration': duration,
                    'voice_id': voice_id, 'pitch_shift': pitch_shift,
                    'clip_sha256': _hash(directory / 'clip.wav'), 'clip_stats': info,
                    'separation': {'state': 'new' if source is not None else 'skipped', 'job_id': None},
                    'conversion': {'state': 'new', 'job_id': None}, 'artifacts': artifacts}
        self._save(directory, manifest)
        return self.status(session)

    def status(self, session: str) -> dict:
        directory = self._directory(session)
        manifest = self._load(directory)
        result = {'session': session, 'state': manifest['state'], 'voiceId': manifest['voice_id'],
                  'duration': manifest['duration'], 'clip': str(directory / 'clip.wav'),
                  'separation': manifest['separation'], 'conversion': manifest['conversion']}
        if manifest['state'] == 'complete':
            result['output'] = str(directory / 'cover.wav')
            result['audio'] = manifest.get('output_stats')
        return result

    def _job(self, directory, manifest, kind, audio, *, max_polls):
        stage = manifest[kind]
        client = self._provider()
        if stage['state'] == 'submitting':
            raise CoverError('cover_submission_unknown')
        if stage['state'] == 'new':
            stage['state'] = 'submitting'
            self._save(directory, manifest)
            # A failed or lost response intentionally leaves the write-ahead
            # marker intact; this directory can never auto-submit this stage again.
            if kind == 'separation':
                job = client.submit_separation(audio)
            else:
                job = client.submit_conversion(audio, manifest['voice_id'], manifest['pitch_shift'])
            stage.update(state='submitted', job_id=job['id'])
            self._save(directory, manifest)
        for attempt in range(max_polls):
            job = client.get_job(kind, stage['job_id'])
            if kind == 'conversion' and job.get('voiceModelId', manifest['voice_id']) != manifest['voice_id']:
                raise CoverError('cover_job_model_mismatch')
            if job['status'] == 'success':
                return job
            if job['status'] in ('error', 'cancelled'):
                raise CoverError('cover_job_failed')
            if attempt < max_polls - 1:
                self.sleep(5)
        raise CoverError('cover_poll_timeout')

    def _artifact(self, directory, manifest, name, url):
        path = directory / (name + '.wav')
        expected = manifest['artifacts'].get(name)
        if expected and path.is_file() and not path.is_symlink() and _hash(path) == expected:
            return path
        if path.exists():
            raise CoverError('cover_artifact_unverified')
        if not isinstance(url, str) or not url:
            raise CoverError('cover_output_missing')
        # Raw results are temporary and never enter the manifest. Resume asks the
        # job API for fresh URLs, rather than persisting expiring signed links.
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(dir=directory) as temporary:
            source = Path(temporary) / 'result.audio'
            canonical = Path(temporary) / 'canonical.wav'
            self._provider().download(url, source)
            self._audio().decode(source, canonical, duration=manifest['duration'])
            info = inspect_wav(canonical)
            if abs(info['duration'] - manifest['duration']) > 0.15:
                raise CoverError('audio_duration_mismatch')
            # Write the checksum before publishing. A crash before the link
            # leaves no untracked final file; resume can safely download again.
            manifest['artifacts'][name] = _hash(canonical)
            self._save(directory, manifest)
            os.link(canonical, path)
        return path

    def run(self, session: str, *, max_polls: int = 60) -> dict:
        if type(max_polls) is not int or not 1 <= max_polls <= 60:
            raise CoverError('cover_poll_limit_invalid')
        directory = self._directory(session)
        if not directory.is_dir():
            raise CoverError('cover_session_invalid')
        try:
            with SingleInstance(directory / 'sample.lock'):
                return self._run(directory, session, max_polls)
        except GatewayError:
            raise CoverError('cover_already_running') from None

    def _run(self, directory, session, max_polls):
        manifest = self._load(directory)
        if manifest['state'] == 'complete':
            if _hash(directory / 'cover.wav') != manifest['artifacts'].get('cover'):
                raise CoverError('cover_artifact_unverified')
            return self.status(session)
        clip = directory / 'clip.wav'
        if clip.is_symlink() or _hash(clip) != manifest['clip_sha256']:
            raise CoverError('cover_clip_changed')
        self._audio()
        if manifest['voice_id'] not in self.config.female_voice_ids:
            raise CoverError('cover_voice_not_approved')
        voice = self._provider().get_voice(manifest['voice_id'])
        if not voice['isUsable']:
            raise CoverError('cover_voice_unusable')
        labels = ' '.join([voice['title'], *voice['tags']])
        if not re.search(r'\bfemale\b|女声|女性', labels, re.IGNORECASE):
            raise CoverError('cover_voice_not_female')
        manifest['state'] = 'processing'
        self._save(directory, manifest)
        vocal_url = backing_url = None
        if manifest['separation']['state'] != 'skipped':
            separation = self._job(directory, manifest, 'separation', clip, max_polls=max_polls)
            vocal_url = separation.get('vocalAudioFileUrl')
            backing_url = next((x.get('url') for x in separation.get('stemFileUrls') or []
                                if isinstance(x, dict) and x.get('instrument') == 'backing'), None)
            backing_url = backing_url or separation.get('backingAudioFileUrl')
        vocal = self._artifact(directory, manifest, 'vocal', vocal_url)
        backing = self._artifact(directory, manifest, 'backing', backing_url)
        conversion = self._job(directory, manifest, 'conversion', vocal, max_polls=max_polls)
        converted = self._artifact(directory, manifest, 'converted', conversion.get('outputFileUrl'))
        output = directory / 'cover.wav'
        if output.exists():
            # A crash between publishing the mix and saving the manifest is safe
            # to recover locally: validate and keep it, without another API POST.
            info = inspect_wav(output)
            if abs(info['duration'] - manifest['duration']) > 0.15:
                raise CoverError('audio_duration_mismatch')
        else:
            info = mix_wav(converted, backing, output)
        manifest.update(state='complete', output_stats=info)
        manifest['artifacts']['cover'] = _hash(output)
        self._save(directory, manifest)
        return self.status(session)


def add_cover_parser(commands):
    cover = commands.add_parser('cover', help='本机云端翻唱短样，不发送微信')
    modes = cover.add_subparsers(dest='cover_command', required=True)
    modes.add_parser('check', help='只检查本地配置和音频工具，不联网')
    voices = modes.add_parser('voices', help='查询 Kits 声音目录，不提交生成任务')
    voices.add_argument('--page', type=int, default=1)
    prepare = modes.add_parser('prepare', help='仅裁剪本地试听片段，不上传')
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path)
    source.add_argument('--vocal', type=Path)
    prepare.add_argument('--backing', type=Path)
    prepare.add_argument('--song', required=True)
    prepare.add_argument('--artist', required=True)
    prepare.add_argument('--start', type=float, required=True)
    prepare.add_argument('--duration', type=float, default=20)
    prepare.add_argument('--voice-id', type=int, required=True)
    prepare.add_argument('--pitch-shift', type=int, default=0)
    prepare.add_argument('--session')
    for command in ('run', 'status'):
        mode = modes.add_parser(command, help='执行/恢复云端短样' if command == 'run' else '查看本地任务状态')
        mode.add_argument('--session', required=True)
        if command == 'run':
            mode.add_argument('--max-polls', type=int, default=60)


def run_cover(args, emit):
    config = CoverConfig.from_env()
    if args.cover_command == 'check':
        try:
            AudioTools.discover(config.ffmpeg)
            ready = True
        except CoverError:
            ready = False
        emit({'provider': 'kits', 'keyConfigured': bool(config.api_key), 'ffmpegReady': ready,
              'downloadHostCount': len(config.download_hosts), 'networkChecked': False,
              'wechatSent': False})
        return
    if args.cover_command == 'voices':
        emit(KitsClient(config.api_key, config.download_hosts).list_voices(page=args.page))
        return
    sample = CoverSample(config)
    if args.cover_command == 'prepare':
        result = sample.prepare(args.input, song=args.song, artist=args.artist, start=args.start,
                                duration=args.duration, voice_id=args.voice_id,
                                pitch_shift=args.pitch_shift, session=args.session,
                                vocal=args.vocal, backing=args.backing)
    elif args.cover_command == 'status':
        result = sample.status(args.session)
    else:
        result = sample.run(args.session, max_polls=args.max_polls)
    emit(result)
