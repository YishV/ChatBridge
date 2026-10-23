import io
import json
import unittest
from unittest.mock import Mock, patch

from chat_bridge.models.client import ChatCompletionsClient, ModelError


class WeatherModelTests(unittest.TestCase):
    def call(self, plan, *, calls=1, name='plan_weather', finish='tool_calls'):
        client = ChatCompletionsClient(base_url='https://api.deepseek.com',
            allowed_hosts=frozenset({'api.deepseek.com'}), api_key='fixture', model='fixture')
        body = {'choices':[{'finish_reason':finish,'message':{'content':'', 'tool_calls':[
            {'type':'function','function':{'name':name,'arguments':json.dumps(plan)}} for _ in range(calls)]}}]}
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(body).encode())
        with patch('chat_bridge.models.client.build_opener',return_value=opener):
            result = client.complete_weather([{'role':'user','content':'北京明天要带伞吗'}])
        return result, json.loads(opener.open.call_args.args[0].data)

    def test_weather_plan_uses_one_forced_tool_without_json_output_mode(self):
        plan = {'action':'query','city':'北京','start_date':'2026-10-22','days':1}
        result,payload = self.call(plan)
        self.assertEqual(result,plan)
        self.assertEqual(payload['tool_choice']['function']['name'],'plan_weather')
        self.assertNotIn('response_format',payload)
        self.assertNotIn('meme_id',str(payload['tools']))

    def test_missing_city_is_a_clarification_not_a_guess(self):
        plan = {'action':'clarify','city':'','start_date':'','days':1}
        self.assertEqual(self.call(plan)[0],plan)

    def test_extra_fields_wrong_tools_and_bad_types_are_rejected(self):
        base = {'action':'query','city':'北京','start_date':'','days':1}
        for override in [{'days':True},{'days':8},{'city':'https://evil.test/'},
                         {'action':'run_shell'},{'start_date':'tomorrow'}, {'url':'https://evil.test'}]:
            with self.subTest(override=override),self.assertRaises((ModelError,ValueError)):
                self.call({**base,**override})
        for extra in [{'calls':2},{'name':'other'}]:
            with self.subTest(extra=extra),self.assertRaises(ModelError):
                self.call(base,**extra)


if __name__=='__main__': unittest.main()
