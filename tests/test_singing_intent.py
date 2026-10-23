import unittest

from chat_bridge.singing.intent import SingingRequest, parse_request


class SingingIntentTests(unittest.TestCase):
    def test_command_with_title(self):
        request = parse_request('/翻唱 天空之城', is_group=False, mentioned=False)
        self.assertEqual(request, SingingRequest(query='天空之城'))

    def test_random_command_and_natural_language(self):
        self.assertTrue(parse_request('/随机翻唱', is_group=False, mentioned=False).random)
        self.assertTrue(parse_request('随便唱一首', is_group=False, mentioned=False).random)
        self.assertEqual(
            parse_request('唱首天空之城', is_group=False, mentioned=False).query,
            '天空之城',
        )

    def test_group_requires_mention_or_slash(self):
        self.assertIsNone(parse_request('唱歌吗', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('/翻唱 天空之城', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('唱首天空之城', is_group=True, mentioned=True))

    def test_ordinary_group_chat_does_not_trigger(self):
        self.assertIsNone(parse_request('今天唱歌吗', is_group=True, mentioned=False))

    def test_explicit_group_song_request_does_not_require_mention(self):
        self.assertTrue(parse_request('唱首歌', is_group=True, mentioned=False).random)
        self.assertEqual(
            parse_request('让ai唱首天空之城', is_group=True, mentioned=False).query,
            '天空之城',
        )

    def test_listen_suffixes_do_not_become_song_titles(self):
        self.assertTrue(parse_request('唱首歌听听', is_group=False, mentioned=False).random)
        self.assertTrue(parse_request('唱首听听', is_group=False, mentioned=False).random)
        self.assertEqual(
            parse_request('唱首天空之城听听', is_group=False, mentioned=False).query,
            '天空之城',
        )

    def test_polite_prefixes_and_point_command(self):
        self.assertEqual(
            parse_request('能不能唱首天空之城', is_group=False, mentioned=False).query,
            '天空之城',
        )
        self.assertEqual(
            parse_request('点歌 天空之城', is_group=False, mentioned=False).query,
            '天空之城',
        )
        self.assertTrue(parse_request('你唱首歌', is_group=False, mentioned=False).random)


if __name__ == '__main__':
    unittest.main()
