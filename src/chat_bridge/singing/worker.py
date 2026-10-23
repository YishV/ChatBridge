"""Background worker that renders queued covers and sends the result."""

import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path

from chat_bridge.singing.renderer import RenderError


_ERROR_TEXT = {
    'separation_failed': '人声分离没成功，换一首试试吧。',
    'render_subprocess_failed': '翻唱引擎执行失败，等会儿再试。',
    'rvc_failed': '音色转换没成功，换一首试试吧。',
    'render_empty': '这次没有生成有效音频，等会儿再试。',
    'source_invalid': '这个音频文件读不了，换一个格式试试。',
    'ffmpeg_missing': '本机音频工具缺失，先检查 ffmpeg 配置。',
}


class SingingWorker:
    def __init__(self, settings, queue, renderer, sender):
        self.settings = settings
        self.queue = queue
        self.renderer = renderer
        self.sender = sender

    def run_once(self) -> bool:
        job = self.queue.claim_next()
        if job is None:
            return False
        source = Path(job.song_path)
        output = self._cache_path(source)
        try:
            self.settings.output_dir.mkdir(parents=True, exist_ok=True)
            if not output.is_file() or output.stat().st_size == 0:
                self.renderer.render(source, output)
            if not output.is_file() or output.stat().st_size == 0:
                raise RenderError('render_empty')
            if self.settings.send_mp3:
                safe_name = re.sub(r'[\\/:*?"<>|]+', '_', job.query).strip()[:60] or 'cover'
                receipt = self.sender.send_group_file(
                    job.conversation_id, output, f'{safe_name}-cover.mp3'
                )
                if not getattr(receipt, 'confirmed', False):
                    self._send_voice(job, output)
                    self.sender.send_group_text(
                        job.conversation_id, '文件发送失败，先发 30 秒语音条。'
                    )
            else:
                self._send_voice(job, output)
            self.queue.complete(job.id, str(output))
            self.sender.send_group_text(
                job.conversation_id, f'《{job.query}》翻唱完成了。'
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, RenderError) else 'render_failed'
            self.queue.fail(job.id, code)
            self.sender.send_group_text(
                job.conversation_id,
                _ERROR_TEXT.get(code, '这次翻唱失败了，换一首试试吧。'),
            )
        return True

    def run_forever(self):
        while True:
            if not self.run_once():
                time.sleep(self.settings.poll_seconds)

    def _cache_path(self, source: Path) -> Path:
        digest = hashlib.sha256()
        digest.update(source.read_bytes())
        digest.update(self.settings.rvc_model.encode('utf-8'))
        digest.update(str(self.settings.pitch).encode('ascii'))
        return self.settings.output_dir / (digest.hexdigest()[:32] + '.mp3')

    def _send_voice(self, job, output: Path):
        excerpt = output.with_suffix('.30s.wav')
        temp_dir = self.settings.rvc_home / '.work'
        temp_dir.mkdir(parents=True, exist_ok=True)
        temporary = temp_dir / (output.stem + '.30s.wav')
        self._excerpt(output, temporary)
        shutil.copyfile(temporary, excerpt)
        self.sender.send_voice_file(job.conversation_id, excerpt)

    def _excerpt(self, source: Path, output: Path):
        subprocess.run([
            str(self.settings.ffmpeg), '-y', '-t', str(self.settings.voice_excerpt_seconds),
            '-i', str(source), '-ac', '1', '-ar', '32000', str(output),
        ], check=True)
