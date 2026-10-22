import unittest

from wechat_ai_bridge.singing.intent import SingingRequest, parse_request


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
        self.assertIsNone(parse_request('唱首天空之城', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('/翻唱 天空之城', is_group=True, mentioned=False))
        self.assertIsNotNone(parse_request('唱首天空之城', is_group=True, mentioned=True))

    def test_ordinary_group_chat_does_not_trigger(self):
        self.assertIsNone(parse_request('今天唱歌吗', is_group=True, mentioned=False))


if __name__ == '__main__':
    unittest.main()
