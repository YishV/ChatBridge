import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.settings import Settings


class VisionSettingsTests(unittest.TestCase):
    def settings(self,**extra):
        return Settings(state_db=Path('fixture.db'),model_name='fixture',model_base_url='https://fixture.test',
            allowed_model_hosts=frozenset({'fixture.test'}),model_api_key='',**extra)

    def test_vision_enabled_by_default_with_short_window(self):
        settings=self.settings()
        self.assertTrue(settings.vision_enabled)
        self.assertEqual(settings.vision_window_seconds,120)

    def test_invalid_vision_settings_are_rejected(self):
        for values in [{'vision_enabled':'true'},{'vision_window_seconds':0},{'vision_window_seconds':301},
                       {'vision_window_seconds':True}]:
            with self.subTest(values=values),self.assertRaises(ValueError):self.settings(**values)


if __name__=='__main__':unittest.main()
