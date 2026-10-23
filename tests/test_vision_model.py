import base64
import io
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from chat_bridge.models.client import ChatCompletionsClient
from chat_bridge.vision.media import normalize_image


class VisionModelTests(unittest.TestCase):
    def test_image_is_only_in_last_user_message_and_input_is_not_mutated(self):
        buffer=io.BytesIO();Image.new('RGB',(16,16),'blue').save(buffer,'PNG')
        image=normalize_image(buffer.getvalue())
        messages=[{'role':'system','content':'按人设回复'},
                  {'role':'user','content':'看这张图','name':'member_012345abcdef'}]
        client=ChatCompletionsClient(base_url='https://api.deepseek.com',
            allowed_hosts=frozenset({'api.deepseek.com'}),api_key='fixture',model='deepseek-flash')
        opener=Mock();opener.open.return_value=io.BytesIO(json.dumps({'choices':[{
            'finish_reason':'stop','message':{'content':'一张蓝色图片。'}}]}).encode())
        with patch('chat_bridge.models.client.build_opener',return_value=opener):
            reply=client.complete_vision(messages,image=image)
        payload=json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(reply,'一张蓝色图片。')
        content=payload['messages'][-1]['content']
        self.assertEqual(content[0],{'type':'text','text':'看这张图'})
        self.assertEqual(base64.b64decode(content[1]['image_url']['url'].split(',',1)[1]),image.data)
        self.assertEqual(payload['messages'][-1]['name'],'member_012345abcdef')
        self.assertEqual(messages[-1]['content'],'看这张图')
        self.assertNotIn('image_url',str(payload['messages'][0]))

    def test_requires_a_last_user_message_and_normalized_image(self):
        client=ChatCompletionsClient(base_url='https://api.deepseek.com',allowed_hosts=frozenset({'api.deepseek.com'}),
            api_key='fixture',model='deepseek-flash')
        with self.assertRaises(ValueError):
            client.complete_vision([{'role':'system','content':'no user'}],image=b'unsafe')


if __name__=='__main__':unittest.main()
