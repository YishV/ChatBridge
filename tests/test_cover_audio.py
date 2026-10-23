import math
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from chat_bridge.media.audio import AudioTools, inspect_wav, mix_wav
from chat_bridge.media.kits import CoverError


def tone(path, seconds=3, amplitude=8000, frequency=440):
    values = array('h')
    for i in range(round(44100 * seconds)):
        value = round(amplitude * math.sin(i * 2 * math.pi * frequency / 44100))
        values.extend((value, value))
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        output.writeframes(values.tobytes())


class CoverAudioTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_wav_inspection_rejects_silent_or_truncated_audio(self):
        source = self.root / 'tone.wav'
        tone(source)
        info = inspect_wav(source)
        self.assertAlmostEqual(info['duration'], 3)
        self.assertGreater(info['rms'], 0.1)
        tone(source, amplitude=0)
        with self.assertRaisesRegex(CoverError, 'audio_silent'):
            inspect_wav(source)
        tone(source)
        source.write_bytes(source.read_bytes()[:-100])
        with self.assertRaisesRegex(CoverError, 'audio_invalid'):
            inspect_wav(source)

    def test_mix_preserves_duration_and_limits_peak_without_clipping(self):
        vocal, backing = self.root / 'v.wav', self.root / 'b.wav'
        tone(vocal, amplitude=28000)
        tone(backing, amplitude=28000)
        result = self.root / 'mix.wav'
        info = mix_wav(vocal, backing, result)
        self.assertAlmostEqual(info['duration'], 3)
        self.assertLessEqual(info['peak'], 0.951)
        self.assertGreater(info['rms'], 0.2)
        with self.assertRaisesRegex(CoverError, 'audio_output_exists'):
            mix_wav(vocal, backing, result)

    def test_mix_rejects_mismatched_durations(self):
        vocal, backing = self.root / 'v.wav', self.root / 'b.wav'
        tone(vocal, seconds=3)
        tone(backing, seconds=4)
        with self.assertRaisesRegex(CoverError, 'audio_duration_mismatch'):
            mix_wav(vocal, backing, self.root / 'mix.wav')

    def test_failed_mix_publication_leaves_no_partial_final_file(self):
        vocal, backing = self.root / 'v.wav', self.root / 'b.wav'
        tone(vocal)
        tone(backing)
        with patch('chat_bridge.media.audio.os.link', side_effect=OSError('fixture publish failure')):
            with self.assertRaises(OSError):
                mix_wav(vocal, backing, self.root / 'mix.wav')
        self.assertFalse((self.root / 'mix.wav').exists())

    def test_non_finite_or_out_of_range_times_never_launch_decoder(self):
        tools = AudioTools('fixture-ffmpeg')
        with patch('chat_bridge.media.audio.subprocess.run') as run:
            for start, duration in [(float('nan'), 5), (0, float('inf')), (-1, 5), (0, 31), (0, 0)]:
                with self.subTest(start=start, duration=duration), self.assertRaises(CoverError):
                    tools.decode(self.root / 'missing', self.root / 'out.wav', start=start, duration=duration)
            run.assert_not_called()

    def test_decode_clips_real_audio_and_rejects_end_beyond_source(self):
        try:
            tools = AudioTools.discover()
        except CoverError:
            self.skipTest('install media extra to exercise FFmpeg')
        source = self.root / 'tone.wav'
        tone(source, seconds=4)
        destination = self.root / 'clip.wav'
        info = tools.decode(source, destination, start=1, duration=3)
        self.assertAlmostEqual(info['duration'], 3, places=2)
        with self.assertRaisesRegex(CoverError, 'audio_duration_mismatch'):
            tools.decode(source, self.root / 'short.wav', start=3, duration=3)
        self.assertFalse((self.root / 'short.wav').exists())

    def test_decoder_disables_network_and_uses_argument_array(self):
        source, destination = self.root / 'in.wav', self.root / 'out.wav'
        tone(source)
        def run(args, **kwargs):
            self.assertIn('file,pipe', args)
            self.assertIn('wav,mp3,flac', args)
            self.assertFalse(kwargs.get('shell', False))
            self.assertIn('-nostdin', args)
            tone(Path(args[-1]))
        with patch('chat_bridge.media.audio.subprocess.run', side_effect=run):
            AudioTools('fixture-ffmpeg').decode(source, destination, duration=3)


if __name__ == '__main__':
    unittest.main()
