import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.singing.config import SingingSettings
from wechat_ai_bridge.singing.queue import SingingQueue
from wechat_ai_bridge.singing.worker import SingingWorker
from wechat_ai_bridge.wechat.delivery import SendReceipt


class FakeSender:
    def __init__(self):
        self.texts = []
        self.files = []
        self.voices = []

    def send_group_text(self, conversation, text):
        self.texts.append((conversation, text))
        return True

    def send_group_file(self, conversation, path, name=None):
        self.files.append((conversation, path))
        return SendReceipt(True, 'file-1')

    def send_voice_file(self, conversation, path):
        self.voices.append((conversation, path))
        return True


class FakeRenderer:
    def render(self, source, output):
        Path(output).write_bytes(b'ID3')
        return Path(output)


class SingingWorkerTests(unittest.TestCase):
    def test_worker_sends_file_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'
            source.write_bytes(b'audio')
            settings = SingingSettings(
                enabled=True, music_dir=root, send_mp3=True,
                output_dir=root / 'covers',
            )
            queue = SingingQueue(root / 'q.sqlite3')
            queue.enqueue('private:1', False, '1', 'song', str(source))
            sender = FakeSender()
            worker = SingingWorker(settings, queue, FakeRenderer(), sender)
            worker.run_once()
            self.assertIsNone(queue.claim_next())
            self.assertEqual(len(sender.files), 1)
            self.assertEqual(len(sender.voices), 0)
            queue.close()

    def test_worker_sends_voice_excerpt_when_mp3_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'
            source.write_bytes(b'audio')
            settings = SingingSettings(
                enabled=True, music_dir=root, send_mp3=False,
                output_dir=root / 'covers', ffmpeg=Path('ffmpeg'),
            )
            queue = SingingQueue(root / 'q.sqlite3')
            queue.enqueue('private:1', False, '1', 'song', str(source))
            sender = FakeSender()
            worker = SingingWorker(settings, queue, FakeRenderer(), sender)
            worker._excerpt = lambda source, output: output.write_bytes(b'RIFF')
            worker.run_once()
            self.assertEqual(len(sender.voices), 1)
            self.assertEqual(len(sender.files), 0)
            queue.close()


if __name__ == '__main__':
    unittest.main()
