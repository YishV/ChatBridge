import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.singing.queue import QueueBusy, SingingQueue


class SingingQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = SingingQueue(Path(self.temp.name) / 'q.sqlite3')

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_enqueue_claim_complete_and_recent(self):
        job_id = self.queue.enqueue('private:1', False, '1', '天空之城', '/music/a.mp3')
        job = self.queue.claim_next()
        self.assertEqual(job.id, job_id)
        self.assertEqual(job.status, 'running')
        self.queue.complete(job_id, '/covers/a.mp3')
        self.assertEqual(self.queue.recent_paths('private:1'), ['/music/a.mp3'])

    def test_same_conversation_cannot_queue_twice(self):
        self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        with self.assertRaises(QueueBusy):
            self.queue.enqueue('private:1', False, '1', 'b', '/b.mp3')

    def test_same_song_in_same_conversation_returns_existing_id(self):
        first = self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        second = self.queue.enqueue('private:1', False, '1', 'a', '/a.mp3')
        self.assertEqual(first, second)

    def test_claim_returns_none_when_empty(self):
        self.assertIsNone(self.queue.claim_next())


if __name__ == '__main__':
    unittest.main()
