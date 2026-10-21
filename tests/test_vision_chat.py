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
    def __init__(self):self.calls=[];self.fail_once=False
    def complete(self,messages):self.calls.append(('text',messages));return '普通文字回复'
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
