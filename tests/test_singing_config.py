import os
import unittest
from pathlib import Path
from unittest.mock import patch

from chat_bridge.singing.config import SingingSettings


class SingingSettingsTests(unittest.TestCase):
    def test_defaults_are_disabled_and_use_project_music_dir(self):
        with patch('chat_bridge.singing.config.load_dotenv'):
            with patch.dict(os.environ, {}, clear=True):
                settings = SingingSettings.from_env()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.music_dir, Path('data/music').resolve())
        self.assertFalse(settings.send_mp3)
        self.assertEqual(settings.voice_excerpt_seconds, 30)

    def test_env_overrides_paths_and_switch(self):
        values = {
            'SINGING_ENABLED': 'true',
            'SINGING_MUSIC_DIR': 'D:/music',
            'SINGING_SEND_MP3': 'true',
            'SINGING_VOICE_EXCERPT_SECONDS': '20',
            'SINGING_PITCH': '5',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = SingingSettings.from_env()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.music_dir, Path('D:/music').resolve())
        self.assertTrue(settings.send_mp3)
        self.assertEqual(settings.voice_excerpt_seconds, 20)
        self.assertEqual(settings.pitch, 5)


if __name__ == '__main__':
    unittest.main()
