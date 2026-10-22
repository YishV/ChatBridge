import tempfile
import unittest
from pathlib import Path

from wechat_ai_bridge.singing.library import SongLibrary


class SongLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fake')
        return path

    def test_scan_extracts_title_artist_and_skips_other_files(self):
        self.touch('天空之城.mp3')
        self.touch('李志 - 你好.wav')
        self.touch('cover.txt')
        library = SongLibrary(self.root)
        songs = library.scan()
        self.assertEqual([song.title for song in songs], ['你好', '天空之城'])
        self.assertEqual(songs[0].artist, '李志')

    def test_match_prefers_exact_then_fuzzy(self):
        self.touch('天空之城.mp3')
        self.touch('天空之城 Live.mp3')
        library = SongLibrary(self.root)
        exact = library.match('天空之城')
        self.assertEqual(exact[0].title, '天空之城')
        fuzzy = library.match('天空之成')
        self.assertTrue(fuzzy)
        self.assertIn('天空之城', fuzzy[0].title)

    def test_random_avoids_excluded_paths(self):
        a = self.touch('a.mp3')
        b = self.touch('b.mp3')
        library = SongLibrary(self.root)
        chosen = library.random(exclude={a.resolve()})
        self.assertEqual(chosen.path, b.resolve())

    def test_nonexistent_root_is_empty(self):
        self.assertEqual(SongLibrary(self.root / 'missing').scan(), [])


if __name__ == '__main__':
    unittest.main()
