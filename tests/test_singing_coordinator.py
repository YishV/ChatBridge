import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.singing.config import SingingSettings
from wechat_ai_bridge.singing.coordinator import SingingCoordinator
from wechat_ai_bridge.singing.library import SongLibrary
from wechat_ai_bridge.singing.queue import SingingQueue


class Sender:
    def __init__(self):
        self.texts = []

    def send_group_text(self, conversation, text):
        self.texts.append((conversation, text))
        return True


class SingingCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'music').mkdir()
        (self.root / 'music' / '天空之城.mp3').write_bytes(b'audio')
        self.queue = SingingQueue(self.root / 'q.sqlite3')
        self.sender = Sender()
        settings = SingingSettings(
            enabled=True, music_dir=self.root / 'music', output_dir=self.root / 'covers'
        )
        self.coordinator = SingingCoordinator(
            settings, SongLibrary(settings.music_dir), self.queue, self.sender
        )

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_title_request_enqueues_and_acknowledges(self):
        message = IncomingMessage('m1', 'private:1', '1', '/翻唱 天空之城', True, is_group=False)
        self.assertTrue(self.coordinator.handle(message))
        job = self.queue.claim_next()
        self.assertIsNotNone(job)
        self.assertTrue(job.song_path.endswith('天空之城.mp3'))
        self.assertIn('开始翻唱《天空之城》', self.sender.texts[0][1])

    def test_group_message_without_mention_is_ignored(self):
        message = IncomingMessage('m1', '1', '2', '唱首天空之城', False, is_group=True)
        self.assertFalse(self.coordinator.handle(message))
        self.assertEqual(self.sender.texts, [])

    def test_missing_song_reports_no_match(self):
        message = IncomingMessage('m1', 'private:1', '1', '/翻唱 不存在的歌', True, is_group=False)
        self.assertTrue(self.coordinator.handle(message))
        self.assertIn('没找到', self.sender.texts[0][1])


if __name__ == '__main__':
    unittest.main()
