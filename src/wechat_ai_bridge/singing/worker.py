"""Background worker that renders queued covers and sends the result."""

import hashlib
import re
import subprocess
import time
from pathlib import Path

from wechat_ai_bridge.singing.renderer import RenderError


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
            self.sender.send_group_text(job.conversation_id, f'翻唱失败：{code}')
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
        self._excerpt(output, excerpt)
        self.sender.send_voice_file(job.conversation_id, excerpt)

    def _excerpt(self, source: Path, output: Path):
        subprocess.run([
            str(self.settings.ffmpeg), '-y', '-t', str(self.settings.voice_excerpt_seconds),
            '-i', str(source), '-ac', '1', '-ar', '32000', str(output),
        ], check=True)
