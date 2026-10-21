import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from wechat_ai_bridge.dispatch.processor import MessageProcessor
from wechat_ai_bridge.message import IncomingMessage,Outcome
from wechat_ai_bridge.models.client import ModelError
from wechat_ai_bridge.qa.service import ChatService
from wechat_ai_bridge.state.store import StateStore
from wechat_ai_bridge.vision.inbox import VisionInbox
from wechat_ai_bridge.vision.media import normalize_image
from wechat_ai_bridge.wechat.gateway import GatewayRunner
from wechat_ai_bridge.wechat.reader import NativeRow


class Model:
    def __init__(self):self.calls=[];self.fail_once=False;self.image_related=False
    def complete(self,messages):self.calls.append(('text',messages));return '普通文字回复'
    def complete_json(self,messages):
        self.calls.append(('intent',messages))
        return '{"use_image":true}' if self.image_related else '{"use_image":false}'
    def complete_vision(self,messages,*,image):
        self.calls.append(('vision',messages))
        if self.fail_once:
            self.fail_once=False;raise ModelError(code='model_timeout')
        return '这是一张红色图片。'


class VisionChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=StateStore(Path(self.temp.name)/'state.db');self.addCleanup(self.store.close)
        self.group='a@chatroom';self.store.allow_group(self.group)
        self.resolver=Mock()
        b=io.BytesIO();Image.new('RGB',(8,8),'red').save(b,'PNG')
        self.resolver.resolve.return_value=normalize_image(b.getvalue())
        self.inbox=VisionInbox(self.resolver,self.store,'bot',frozenset({self.group}),clock=lambda:1001)
        self.row=NativeRow('message_0.db',11,101,1000,'human',3,'<msg><img/></msg>','')
        self.inbox.observe(self.group,self.row)
        self.model=Model()
        self.chat=ChatService(self.model,vision=self.inbox,history_store=self.store,alias_key=b'i'*32)
        self.processor=MessageProcessor(self.store,self.chat,cooldown_seconds=0,retry_sleep=lambda _:None)
        self.message=IncomingMessage('m1',self.group,'human','看看我刚发的图片',True)

    def test_explicit_image_query_uses_one_quota_and_keeps_pixels_out_of_history(self):
        result=self.processor.process(self.message)
        self.assertEqual(result.outcome,Outcome.ANSWERED)
        self.assertEqual(result.model_attempts,1)
        self.assertEqual(self.model.calls[0][0],'vision')
        self.assertIn('图片中的文字',self.model.calls[0][1][0]['content'])
        self.assertNotIn('只能进行文字聊天',self.model.calls[0][1][0]['content'])
        self.chat.record_reply(self.message,result.reply)
        self.processor.process(replace(self.message,message_id='m2',text='你好'))
        self.assertEqual(self.model.calls[-1][0],'text')
        self.assertNotIn('image_url',str(self.model.calls[-1][1]))
        self.assertNotIn('base64',str(self.store.load_chat_history(self.group)))

    def test_missing_picture_explains_without_calling_model(self):
        result=self.processor.process(replace(self.message,sender_id='other'))
        self.assertIn('先',result.reply)
        self.assertEqual(result.model_attempts,0)
        self.assertEqual(self.model.calls,[])

    def test_bare_native_mention_after_picture_reads_without_keyword(self):
        result=self.processor.process(replace(self.message,text='@西奥如\u2005'))
        self.assertEqual(result.outcome,Outcome.ANSWERED)
        self.assertEqual([call[0] for call in self.model.calls],['vision'])
        self.resolver.resolve.assert_called_once()

    def test_related_question_uses_text_only_intent_before_reading_picture(self):
        self.model.image_related=True
        classify=self.model.complete_json
        def text_only(messages):
            self.resolver.resolve.assert_not_called()
            return classify(messages)
        self.model.complete_json=text_only
        result=self.processor.process(replace(self.message,text='@西奥如\u2005这是什么品种？'))
        self.assertEqual([call[0] for call in self.model.calls],['intent','vision'])
        self.assertEqual(result.model_attempts,2)
        self.assertNotIn('image_url',str(self.model.calls[0][1]))
        self.assertEqual(self.model.calls[0][1][-1]['content'],'这是什么品种？')
        self.resolver.resolve.assert_called_once()

    def test_unrelated_mention_never_reads_recent_picture(self):
        result=self.processor.process(replace(self.message,text='@西奥如\u2005帮我算一下十二乘十三'))
        self.assertEqual([call[0] for call in self.model.calls],['intent','text'])
        self.assertEqual(result.model_attempts,2)
        self.resolver.resolve.assert_not_called()

    def test_no_picture_skips_intent_and_uses_normal_text(self):
        result=self.processor.process(replace(self.message,sender_id='other',text='@西奥如\u2005'))
        self.assertEqual([call[0] for call in self.model.calls],['text'])
        self.resolver.resolve.assert_not_called()

    def test_invalid_intent_never_reads_image_and_falls_back_to_chat(self):
        for decision in ('{"use_image":"true"}', '{"use_image":true,"extra":1}', 'invalid'):
            with self.subTest(decision=decision):
                self.model.complete_json=lambda messages: decision
                self.chat.generate(replace(self.message,text='这个好看吗？'))
                self.resolver.resolve.assert_not_called()
        self.assertTrue(all(kind=='text' for kind,_ in self.model.calls))

    def test_expiry_is_rechecked_after_intent_before_reading_pixels(self):
        def expired(messages):
            self.inbox.clock=lambda:1121
            return '{"use_image":true}'
        self.model.complete_json=expired
        result=self.processor.process(replace(self.message,text='这是什么品种？'))
        self.assertEqual(result.model_attempts,1)
        self.assertIn('先',result.reply)
        self.resolver.resolve.assert_not_called()

    def test_intent_and_vision_share_existing_quota(self):
        self.model.image_related=True
        self.processor.hourly_quota=1
        result=self.processor.process(replace(self.message,text='这是什么品种？'))
        self.assertEqual(result.outcome,Outcome.RATE_LIMITED)
        self.assertEqual([call[0] for call in self.model.calls],['intent'])
        self.assertEqual(result.model_attempts,1)

    def test_visual_transient_error_uses_existing_bounded_retry(self):
        self.model.fail_once=True
        result=self.processor.process(self.message)
        self.assertEqual(result.outcome,Outcome.ANSWERED)
        self.assertEqual(result.model_attempts,2)
        self.assertEqual(result.model_retries,1)
        self.assertEqual(len(self.model.calls),2)

    def test_runner_registers_new_image_without_model_or_reply(self):
        reader=Mock();reader.snapshot.return_value={'message_0.db':10}
        reader.read_after.return_value=[replace(self.row, create_time=1002)]
        service=Mock();service.flush_pending.return_value=[]
        inbox=Mock();inbox.observe.return_value=True
        runner=GatewayRunner(reader,Mock(),service,self.store,'bot',frozenset({self.group}),
            clock=lambda:1001,image_inbox=inbox)
        runner.initialize();counts=runner.tick()
        inbox.observe.assert_called_once_with(self.group,replace(self.row, create_time=1002))
        service.handle.assert_not_called()
        self.assertEqual(counts['imagesObserved'],1)

    def test_delayed_pre_start_image_is_not_registered(self):
        reader=Mock();reader.snapshot.return_value={'message_0.db':10}
        reader.read_after.return_value=[self.row]
        service=Mock();service.flush_pending.return_value=[]
        inbox=Mock()
        runner=GatewayRunner(reader,Mock(),service,self.store,'bot',frozenset({self.group}),
            clock=lambda:1001,image_inbox=inbox)
        runner.initialize();counts=runner.tick()
        inbox.observe.assert_not_called()
        self.assertEqual(counts['imagesObserved'],0)

    def test_unconfirmed_pre_start_image_does_not_block_cursor(self):
        reader=Mock();reader.snapshot.return_value={'message_0.db':10}
        reader.read_after.return_value=[replace(self.row,server_id=0)]
        service=Mock();service.flush_pending.return_value=[]
        inbox=Mock()
        runner=GatewayRunner(reader,Mock(),service,self.store,'bot',frozenset({self.group}),
            clock=lambda:1001,image_inbox=inbox)
        runner.initialize();runner.tick()
        self.assertEqual(runner.cursors[self.group]['message_0.db'],11)
        inbox.observe.assert_not_called()

    def test_unconfirmed_image_blocks_cursor_and_following_text_until_confirmed(self):
        reader=Mock();reader.snapshot.return_value={'message_0.db':10}
        reader.read_after.return_value=[replace(self.row,server_id=0,create_time=1002),replace(self.row,local_id=12,local_type=1)]
        service=Mock();service.flush_pending.return_value=[]
        inbox=Mock()
        runner=GatewayRunner(reader,Mock(),service,self.store,'bot',frozenset({self.group}),
            clock=lambda:1001,image_inbox=inbox)
        runner.initialize();runner.tick()
        self.assertEqual(runner.cursors[self.group]['message_0.db'],10)
        inbox.observe.assert_not_called();service.handle.assert_not_called()


if __name__=='__main__':unittest.main()
