import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.state.store import StateStore


class MemeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.db'
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.store.allow_group('group-a')

    def test_media_reservation_survives_restart_and_blocks_duplicates(self):
        self.assertTrue(self.store.begin_media_delivery('m1', 'group-a', '083', cooldown_seconds=60))
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        self.assertFalse(reopened.begin_media_delivery('m1', 'group-a', '083', cooldown_seconds=0))
        self.assertFalse(reopened.begin_media_delivery('m2', 'group-a', '123', cooldown_seconds=60))
        self.assertEqual(reopened.recent_media('group-a'), ['083'])
        self.assertFalse(reopened.media_available('group-a', cooldown_seconds=60))

    def test_pause_and_whitelist_gate_image_reservations(self):
        self.assertFalse(self.store.begin_media_delivery('m1', 'unknown', '083', cooldown_seconds=0))
        self.store.set_paused(True)
        self.assertFalse(self.store.begin_media_delivery('m2', 'group-a', '083', cooldown_seconds=0))
        self.assertEqual(self.store.recent_media('group-a'), [])

    def test_only_confirmed_image_is_context_and_finish_is_idempotent(self):
        chat = {'member_id': 'member_012345abcdef', 'sender_name': None,
                'content': '[已发送表情包 #083：开心偷笑]', 'interaction': 'text'}
        self.store.begin_media_delivery('m1', 'group-a', '083', cooldown_seconds=0)
        self.store.finish_media_delivery('m1', confirmed=True, server_message_id='image-1', chat=chat)
        self.store.finish_media_delivery('m1', confirmed=True, server_message_id='image-1', chat=chat)
        rows = self.store.load_chat_history('group-a')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content'], chat['content'])
        self.assertEqual(rows[0]['role'], 'assistant')
        self.store.begin_media_delivery('m2', 'group-a', '123', cooldown_seconds=0)
        self.store.finish_media_delivery('m2', confirmed=False, chat=chat)
        self.assertEqual(len(self.store.load_chat_history('group-a')), 1)
        self.store.deny_group('group-a')
        self.assertEqual(self.store.recent_media('group-a'), [])

    def test_group_cooldown_and_recent_choices_are_isolated(self):
        self.store.allow_group('group-b')
        self.store.begin_media_delivery('m1', 'group-a', '083', cooldown_seconds=0)
        self.assertTrue(self.store.media_available('group-b', cooldown_seconds=60))
        self.assertEqual(self.store.recent_media('group-b'), [])


if __name__ == '__main__':
    unittest.main()
