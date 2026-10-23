import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chat_bridge import cli
from chat_bridge.settings import Settings


class WeatherCliTests(unittest.TestCase):
    def settings(self, root, **extra):
        return Settings(state_db=root/'state.db', model_name='fixture', model_base_url='https://fixture.test',
            allowed_model_hosts=frozenset({'fixture.test'}),model_api_key='',**extra)

    def test_local_weather_does_not_use_model_native_or_state_database(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=self.settings(Path(directory))
            weather=Mock()
            weather.lookup.return_value={'status':'ok','source':'Open-Meteo'}
            with patch('sys.argv',['bridge','weather','--city','杭州']), \
                 patch.object(cli.Settings,'from_env',return_value=settings), \
                 patch.object(cli,'create_weather_client',return_value=weather), \
                 patch.object(cli,'StateStore') as store,patch.object(cli,'create_model') as model, \
                 patch.object(cli,'NativeClient') as native,patch('sys.stdout',new_callable=io.StringIO) as output:
                cli.main()
            self.assertIn('Open-Meteo',output.getvalue())
            weather.lookup.assert_called_once_with('杭州')
            store.assert_not_called();model.assert_not_called();native.assert_not_called()

    def test_explicit_disabled_weather_returns_no_client(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(cli.create_weather_client(self.settings(Path(directory),weather_enabled=False)))

    def test_weather_settings_validate_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            for values in [{'weather_enabled':'true'},{'weather_timeout_seconds':float('nan')},
                           {'weather_timeout_seconds':0},{'weather_cache_seconds':-1}]:
                with self.subTest(values=values),self.assertRaises(ValueError):
                    self.settings(Path(directory),**values)


if __name__=='__main__':unittest.main()
