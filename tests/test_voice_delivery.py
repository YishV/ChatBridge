import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.voice.errors import VoiceError
from wechat_ai_bridge.voice.sender import VoiceSendResult, VoiceSender
from wechat_ai_bridge.wechat.delivery import DeliveryOutcome, DeliveryService, SendReceipt
from wechat_ai_bridge.wechat.reader import NativeRow


class Model:
    def complete(self, messages):
        return '啊，我在听。'

    def complete_json(self, messages):
        return '{"reply":"自然回复"}'


class Voice:
    def __init__(self, result=None, error=None):
        self.result = result or VoiceSendResult(True, 'voice-1')
        self.error = error
        self.calls = []

    def send_group_voice(self, group_id, text):
        self.calls.append((group_id, text))
        if self.error:
            raise self.error
        return self.result


class VoiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.store.allow_group('group-a')
        self.chat = ChatService(Model(), history_store=self.store, alias_key=b'v' * 32)
        self.sender = Mock()
        self.sender.send_group_text.return_value = SendReceipt(True, 'text-1')
        self.voice = Voice()
        self.now = 0
        self.processor = MessageProcessor(self.store, self.chat, cooldown_seconds=0,
                                          spontaneous_cooldown_seconds=5, clock=lambda: self.now)
        self.service = DeliveryService(self.processor, self.store, self.sender, voice_sender=self.voice)

    def test_directed_reply_uses_voice_not_text(self):
        message = IncomingMessage('m1', 'group-a', 'human', '在吗', True)
        result = self.service.handle(message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(self.voice.calls, [('group-a', '啊，我在听。')])
        self.sender.send_group_text.assert_not_called()
        rows = self.store.load_chat_history('group-a')
        self.assertEqual(rows[-1]['content'], '啊，我在听。')

    def test_natural_reply_stays_text(self):
        message = IncomingMessage('m2', 'group-a', 'human', '今天如何', False)
        self.assertEqual(self.service.handle(message).delivery, DeliveryOutcome.NOT_SENT)
        self.now = 6
        results = self.service.flush_pending()
        self.assertEqual(results[0].delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(self.voice.calls, [])
        self.sender.send_group_text.assert_called_once_with('group-a', '自然回复')

    def test_unconfirmed_voice_is_not_retried_as_text(self):
        self.voice.result = VoiceSendResult(False)
        message = IncomingMessage('m3', 'group-a', 'human', '在吗', True)
        result = self.service.handle(message)
        self.assertEqual(result.delivery, DeliveryOutcome.UNCONFIRMED)
        self.sender.send_group_text.assert_not_called()
        self.assertEqual(self.store.load_chat_history('group-a')[-1]['role'], 'user')

    def test_pre_send_voice_failure_falls_back_to_text(self):
        self.voice.error = VoiceError('voice_tts_failed')
        message = IncomingMessage('m4', 'group-a', 'human', '在吗', True)
        result = self.service.handle(message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.sender.send_group_text.assert_called_once()
        self.assertEqual(self.store.load_chat_history('group-a')[-1]['content'], '啊，我在听。')

    def test_model_can_choose_voice_for_directed_reply(self):
        class DecisionModel(Model):
            def __init__(self):
                self.voice_enabled = None
            def complete_decision(self, messages, **kwargs):
                self.voice_enabled = kwargs.get('voice_enabled')
                return '{"reply":"这句想用语音说。","voice":true}'

        model = DecisionModel()
        chat = ChatService(model, history_store=self.store, alias_key=b'v' * 32, voice_enabled=True)
        processor = MessageProcessor(self.store, chat, cooldown_seconds=0,
                                     spontaneous_cooldown_seconds=5, clock=lambda: self.now)
        service = DeliveryService(processor, self.store, self.sender,
                                  voice_sender=self.voice, voice_policy='model')
        message = IncomingMessage('m5', 'group-a', 'human', '在吗', True)
        result = service.handle(message)
        self.assertEqual(result.delivery, DeliveryOutcome.CONFIRMED)
        self.assertEqual(model.voice_enabled, True)
        self.assertEqual(self.voice.calls, [('group-a', '这句想用语音说。')])
        self.sender.send_group_text.assert_not_called()


class Reader:
    account_id = 'bot'
    def __init__(self, rows):
        self.rows = rows
        self.before = {'message_0.db': 0}
    def conversation_display_name(self, group_id):
        return '测试群'
    def snapshot(self, group_id):
        return self.before
    def read_after(self, group_id, before):
        return self.rows


class Ui:
    def __init__(self):
        self.calls = []
    def begin(self): self.calls.append('begin')
    def end(self): self.calls.append('end')
    def open_chat(self, name, **kwargs): self.calls.append(('chat', name))
    def focus_input(self): self.calls.append('focus')
    def start_recording(self): self.calls.append('start')
    def stop_recording(self): self.calls.append('stop')


class Audio:
    def __init__(self): self.played = []
    def microphone(self): return _Mic()
    def play(self, path): self.played.append(path)


class _Mic:
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Tts:
    def synthesize(self, text):
        return Path('voice.mp3')


class VoiceSenderTests(unittest.TestCase):
    def sender(self, ui, rows):
        return VoiceSender(Reader(rows), 123, tts=Tts(), audio=Audio(), ui=ui,
                           cache_dir=Path('unused'), receipt_timeout=0.01)

    def test_records_then_confirms_from_new_voice_row(self):
        ui = Ui()
        row = NativeRow('message_0.db', 2, 444, 1, 'bot', 34, '', None)
        result = self.sender(ui, [row]).send_group_voice('group-a', '你好')
        self.assertEqual(result, VoiceSendResult(True, '444'))
        self.assertEqual(ui.calls, ['begin', ('chat', '测试群'), 'focus', 'start', 'stop', 'end'])

    def test_post_start_failure_is_marked_as_possibly_sent(self):
        class BadUi(Ui):
            def stop_recording(self):
                raise RuntimeError('fixture')
        with self.assertRaises(VoiceError) as caught:
            self.sender(BadUi(), []).send_group_voice('group-a', '你好')
        self.assertTrue(caught.exception.sent)


if __name__ == '__main__':
    unittest.main()
