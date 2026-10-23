import unittest

from chat_bridge.message import IncomingMessage
from chat_bridge.qa.service import ChatService


class ClassifierModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, messages):
        self.calls.append(messages)
        return self.response

    def complete(self, messages):
        raise AssertionError('JSON classifier should use complete_json')


class SingingClassifierTests(unittest.TestCase):
    def message(self):
        return IncomingMessage('m1', 'private:1', '1', '今天天气不错，来首歌听听', True, is_group=False)

    def test_cover_with_title(self):
        model = ClassifierModel('{"action":"cover","song":"天空之城","confidence":0.92}')
        result = ChatService(model).classify_singing(self.message())
        self.assertEqual(result, '天空之城')
        self.assertTrue(model.calls)

    def test_random_cover_returns_empty_string(self):
        model = ClassifierModel('{"action":"cover","song":"","confidence":0.8}')
        self.assertEqual(ChatService(model).classify_singing(self.message()), '')

    def test_none_and_low_confidence_do_not_trigger(self):
        for response in (
            '{"action":"none","confidence":0.99}',
            '{"action":"cover","song":"天空之城","confidence":0.2}',
        ):
            with self.subTest(response=response):
                model = ClassifierModel(response)
                self.assertIsNone(ChatService(model).classify_singing(self.message()))


if __name__ == '__main__':
    unittest.main()
