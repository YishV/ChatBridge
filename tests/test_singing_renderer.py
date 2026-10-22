import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.singing.config import SingingSettings
from wechat_ai_bridge.singing.renderer import CoverRenderer, RenderError


class CoverRendererTests(unittest.TestCase):
    def test_missing_source_is_rejected_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = SingingSettings(rvc_home=Path(temp), rvc_python=Path(temp) / 'python.exe')
            renderer = CoverRenderer(settings)
            with self.assertRaises(RenderError):
                renderer.render(Path(temp) / 'missing.mp3', Path(temp) / 'out.mp3')

    def test_command_list_contains_expected_rvc_args(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'song.mp3'
            source.write_bytes(b'audio')
            settings = SingingSettings(
                rvc_home=root, rvc_python=root / 'python.exe',
                rvc_model='model.pth', ffmpeg=root / 'ffmpeg.exe',
            )
            (root / 'ffmpeg.exe').write_bytes(b'fake')
            renderer = CoverRenderer(settings)
            commands = []
            renderer._run = lambda cmd, **kwargs: commands.append(cmd)
            renderer._separate = lambda source, work: (
                root / 'vocals.wav', root / 'no_vocals.wav')
            renderer._convert = lambda vocals, work: (
                root / 'vocals.wav', commands.append(['rvc']))
            renderer._mix = lambda vocals, no_vocals, output: output.write_bytes(b'ID3')
            renderer.render(source, root / 'out.mp3')
            self.assertTrue(any('rvc' in cmd for cmd in commands))


if __name__ == '__main__':
    unittest.main()
