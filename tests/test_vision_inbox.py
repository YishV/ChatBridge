import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from chat_bridge.message import IncomingMessage
from chat_bridge.state.store import StateStore
from chat_bridge.wechat.reader import NativeRow
from chat_bridge.vision.media import normalize_image, VisionError


class VisionInboxTests(unittest.TestCase):
    def setUp(self):
        from chat_bridge.vision.inbox import VisionInbox
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=StateStore(Path(self.temp.name)/'state.db');self.addCleanup(self.store.close)
        self.store.allow_group('a@chatroom');self.store.allow_group('b@chatroom')
        self.now=1000
        self.resolver=Mock()
        b=io.BytesIO();Image.new('RGB',(5,5),'red').save(b,'PNG')
        self.resolver.resolve.return_value=normalize_image(b.getvalue())
        self.inbox=VisionInbox(self.resolver,self.store,'bot',frozenset({'a@chatroom','b@chatroom'}),clock=lambda:self.now)
        self.row=NativeRow('message_0.db',11,101,1000,'human',3,'<msg><img md5="'+'a'*32+'"/></msg>','')
        self.message=IncomingMessage('text-1','a@chatroom','human','看看我刚发的图片',True)

    def test_image_metadata_only_then_same_member_explicit_mention_reads_one(self):
        self.assertTrue(self.inbox.observe('a@chatroom',self.row))
        self.resolver.resolve.assert_not_called()
        prepared=self.inbox.prepare(self.message,guard=lambda:None)
        self.assertIsNotNone(prepared.image)
        ref=self.resolver.resolve.call_args.args[0]
        self.assertEqual((ref.local_id,ref.server_id,ref.group_id,ref.sender_id),(11,101,'a@chatroom','human'))

    def test_cross_member_group_unmentioned_and_plain_text_never_upload(self):
        self.inbox.observe('a@chatroom',self.row)
        for message in [replace(self.message,sender_id='other'),replace(self.message,group_id='b@chatroom')]:
            self.assertIn('图片',self.inbox.prepare(message,guard=lambda:None).reply)
        self.assertIsNone(self.inbox.prepare(replace(self.message,mentioned_bot=False),guard=lambda:None))
        self.assertIsNone(self.inbox.prepare(replace(self.message,text='今天天气如何'),guard=lambda:None))
        self.resolver.resolve.assert_not_called()

    def test_newest_image_replaces_older_and_expiry_does_not_fallback(self):
        self.inbox.observe('a@chatroom',self.row)
        self.now=1001
        self.inbox.observe('a@chatroom',replace(self.row,local_id=12,server_id=102,create_time=1001))
        self.inbox.prepare(self.message,guard=lambda:None)
        self.assertEqual(self.resolver.resolve.call_args.args[0].server_id,102)
        self.now=1122
        prepared=self.inbox.prepare(self.message,guard=lambda:None)
        self.assertIsNone(prepared.image)
        self.assertEqual(self.resolver.resolve.call_count,1)

    def test_unconfirmed_self_and_denied_images_are_not_registered(self):
        for row in [replace(self.row,server_id=0),replace(self.row,sender_id='bot')]:
            self.assertFalse(self.inbox.observe('a@chatroom',row))
        self.assertFalse(self.inbox.observe('not-allowed@chatroom',self.row))
        self.assertIsNone(self.inbox.prepare(self.message,guard=lambda:None).image)

    def test_decode_failure_returns_clear_text_and_does_not_guess(self):
        self.inbox.observe('a@chatroom',self.row)
        self.resolver.resolve.side_effect=VisionError('decode_failed')
        result=self.inbox.prepare(self.message,guard=lambda:None)
        self.assertIsNone(result.image)
        self.assertIn('读取',result.reply)

    def test_image_file_gif_is_registered_but_unsupported_sticker_clears_old_image(self):
        body='<msg><appmsg><type>8</type><appattach><fileext>gif</fileext></appattach></appmsg></msg>'
        self.assertTrue(self.inbox.observe('a@chatroom',replace(self.row,local_type=(8<<32)|49,text=body)))
        self.inbox.observe('a@chatroom',replace(self.row,local_type=47,local_id=12,server_id=102))
        self.assertIsNone(self.inbox.prepare(self.message,guard=lambda:None).image)

    def test_permission_is_rechecked_before_local_decode(self):
        self.inbox.observe('a@chatroom',self.row)
        self.store.deny_group('a@chatroom')
        self.assertIsNone(self.inbox.prepare(self.message,guard=lambda:None).image)
        self.resolver.resolve.assert_not_called()

    def test_negative_or_capability_only_requests_never_read_picture(self):
        self.inbox.observe('a@chatroom', self.row)
        for text in ('别看图，聊点别的', '不要识别我刚发的图片', '不用分析这张图',
                     '不要上传图片，看看我说的话', '你会看图吗？', '你能识别图片吗？',
                     '你会看图片吗？我只问一下功能', '我想知道你能不能识别图片',
                     '你支持看图功能吗？', '你有识别图片的能力吗？', '帮我发送一张图片', '给我上传图片',
                     '他说“看看我刚发的图”，这句话怎么翻译成英语？'):
            with self.subTest(text=text):
                self.assertIsNone(self.inbox.prepare(replace(self.message, text=text), guard=lambda: None))
        self.resolver.resolve.assert_not_called()

    def test_same_second_uses_local_order_not_random_server_identifier(self):
        self.inbox.observe('a@chatroom', replace(self.row, server_id=200))
        self.assertTrue(self.inbox.observe('a@chatroom', replace(self.row, local_id=12, server_id=100)))
        self.inbox.prepare(self.message, guard=lambda: None)
        self.assertEqual(self.resolver.resolve.call_args.args[0].local_id, 12)

    def test_same_second_cross_shard_is_ambiguous_until_a_newer_picture(self):
        self.inbox.observe('a@chatroom', self.row)
        self.assertFalse(self.inbox.observe('a@chatroom', replace(self.row, shard='message_1.db')))
        self.assertFalse(self.inbox.observe('a@chatroom', replace(self.row, shard='message_1.db', local_id=12)))
        self.assertIsNone(self.inbox.prepare(self.message, guard=lambda: None).image)
        self.resolver.resolve.assert_not_called()
        self.assertFalse(self.inbox.observe('a@chatroom', replace(self.row, shard='message_1.db', local_id=13)))
        self.assertIsNone(self.inbox.prepare(self.message, guard=lambda: None).image)
        self.resolver.resolve.assert_not_called()
        self.assertTrue(self.inbox.observe('a@chatroom', replace(self.row, create_time=1001, local_id=13)))


if __name__=='__main__':unittest.main()
