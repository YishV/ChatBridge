import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chat_bridge.dispatch.processor import MessageProcessor
from chat_bridge.message import IncomingMessage, Outcome
from chat_bridge.models.client import ModelError
from chat_bridge.qa.service import ChatService


def report():
    today = datetime.now(timezone(timedelta(hours=8))).date()
    return {'status':'ok','location':'中国/浙江省/杭州市','current':{'time':str(today)+'T12:00',
            'temperature_c':20,'apparent_temperature_c':19,'description':'多云','wind_speed_kmh':8},
            'daily':[{'date':str(today+timedelta(days=i)), 'description':'多云', 'temperature_min_c':14,
                      'temperature_max_c':22,'rain_probability':20} for i in range(7)],
            'timezone':'Asia/Shanghai','retrieved_at':str(today)+'T04:00:00+00:00',
            'source':'Open-Meteo','source_url':'https://open-meteo.com/'}


class Weather:
    def __init__(self, result=None): self.calls=[]; self.result=report() if result is None else result
    def lookup(self,city):
        self.calls.append(city)
        return self.result


class Model:
    def __init__(self, plan=None):
        self.calls=[]; self.plan=plan or {'action':'query','city':'杭州','start_date':'','days':1}
        self.reply='杭州现在20℃，多云。'
        self.before=lambda:None
    def complete_weather(self,messages):
        self.calls.append(('weather',messages)); self.before()
        if isinstance(self.plan,Exception): raise self.plan
        return self.plan
    def complete(self,messages):
        self.calls.append(('reply',messages)); return self.reply
    def complete_decision(self,messages,**kwargs):
        self.calls.append(('decision',messages)); return '{"reply":"杭州现在20℃，多云。"}'


class WeatherChatTests(unittest.TestCase):
    def setUp(self):
        from chat_bridge.state.store import StateStore
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=StateStore(Path(self.temp.name)/'state.db'); self.addCleanup(self.store.close)
        self.store.allow_group('group-a'); self.store.allow_group('group-b')
        self.model=Model(); self.weather=Weather()
        self.chat=ChatService(self.model,alias_key=b'w'*32, weather_client=self.weather)

    def message(self,id='m1',text='杭州现在天气怎么样',sender='human-a',group='group-a',mentioned=True):
        return IncomingMessage(id,group,sender,text,mentioned)

    def process(self, message=None, **kwargs):
        processor=MessageProcessor(self.store,self.chat,cooldown_seconds=0,retry_sleep=lambda _:None,**kwargs)
        return processor.process(message or self.message())

    def test_weather_facts_source_and_time_are_added_without_external_chat_text(self):
        result=self.process()
        self.assertEqual(result.outcome,Outcome.ANSWERED)
        self.assertEqual(self.weather.calls,['杭州'])
        self.assertEqual(result.model_attempts,2)
        self.assertEqual(result.model_retries,0)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0],2)
        self.assertIn('Open-Meteo',result.reply)
        self.assertIn('https://open-meteo.com/',result.reply)
        self.assertIn('20',str(self.model.calls[-1][1]))

    def test_non_weather_chat_has_one_call_and_no_weather_lookup(self):
        self.process(self.message(text='你好'))
        self.assertEqual(len(self.model.calls),1)
        self.assertEqual(self.weather.calls,[])

    def test_common_clear_sky_and_clothing_queries_enter_weather(self):
        for index,text in enumerate(['北京明天会放晴吗','北京明天有太阳吗','杭州明天穿短袖合适吗']):
            with self.subTest(text=text):
                self.process(self.message(id=f'weather-{index}',text=text))
        self.assertEqual(len(self.weather.calls),3)

    def test_missing_values_in_programmatic_fallback_are_unknown_not_none(self):
        self.model.reply=''
        self.weather.result['current']['temperature_c']=None
        self.weather.result['current']['apparent_temperature_c']=None
        result=self.process()
        self.assertIn('未知',result.reply)
        self.assertNotIn('None',result.reply)

    def test_bad_planned_city_cannot_leak_a_chat_sentence_to_geocoder(self):
        self.model.plan={'action':'query','city':'我叫张三住在杭州月薪两万','start_date':'','days':1}
        result=self.process()
        self.assertIn('城市',result.reply)
        self.assertEqual(self.weather.calls,[])

    def test_missing_city_and_ambiguous_city_never_query_fabricated_location(self):
        self.model.plan={'action':'clarify','city':'','start_date':'','days':1}
        result=self.process(self.message(text='明天会下雨吗'))
        self.assertIn('城市',result.reply)
        self.assertEqual(self.weather.calls,[])
        self.assertEqual(len(self.model.calls),1)
        self.model.plan={'action':'query','city':'朝阳','start_date':'','days':1}
        self.weather.result={'status':'ambiguous','choices':[{'label':'辽宁省朝阳市','query':'朝阳,辽宁省'},
                                                          {'label':'北京市朝阳区','query':'朝阳,北京市'}]}
        result=self.process(self.message(id='m2',text='朝阳天气'))
        self.assertIn('辽宁省',result.reply)
        self.assertIn('北京市',result.reply)
        self.assertEqual(len(self.model.calls),2)

    def test_city_followup_is_scoped_to_group_and_member(self):
        self.model.plan={'action':'clarify','city':'','start_date':'','days':1}
        self.process(self.message(text='明天会下雨吗'))
        self.model.plan={'action':'query','city':'杭州','start_date':'','days':1}
        self.process(self.message(id='m2',text='杭州',sender='human-b'))
        self.process(self.message(id='m3',text='杭州',group='group-b'))
        self.assertEqual(self.weather.calls,[])
        self.process(self.message(id='m4',text='杭州'))
        self.assertEqual(self.weather.calls,['杭州'])
        for kind,messages in self.model.calls:
            if kind=='weather': self.assertNotIn('human-b',str(messages))

    def test_api_failure_returns_clear_text_without_second_model_hallucination(self):
        from chat_bridge.weather.client import WeatherError
        self.weather.lookup=lambda _: (_ for _ in ()).throw(WeatherError('weather_timeout'))
        result=self.process()
        self.assertEqual(result.outcome,Outcome.ANSWERED)
        self.assertIn('暂时',result.reply)
        self.assertNotIn('20℃',result.reply)
        self.assertEqual(len(self.model.calls),1)

    def test_successful_query_needs_a_second_quota_slot(self):
        result=self.process(hourly_quota=1)
        self.assertEqual(result.outcome,Outcome.RATE_LIMITED)
        self.assertEqual(len(self.model.calls),1)

    def test_pause_after_planning_prevents_lookup_and_second_model_call(self):
        self.model.before=lambda:self.store.set_paused(True)
        result=self.process()
        self.assertEqual(result.outcome,Outcome.IGNORED)
        self.assertEqual(self.weather.calls,[])
        self.assertEqual(len(self.model.calls),1)

    def test_query_beyond_available_dates_does_not_invent_a_forecast(self):
        self.model.plan={'action':'query','city':'杭州','start_date':'2099-01-01','days':1}
        result=self.process()
        self.assertIn('7天',result.reply)
        self.assertEqual(len(self.model.calls),1)


if __name__=='__main__': unittest.main()
