import copy
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler


class WeatherClientTests(unittest.TestCase):
    def setUp(self):
        # Import inside setUp so the first run reports the missing feature clearly.
        from chat_bridge.weather.client import WeatherClient, WeatherError
        self.WeatherClient = WeatherClient
        self.WeatherError = WeatherError
        self.now = datetime.now(timezone.utc)
        self.local = self.now.astimezone(timezone(timedelta(hours=8)))
        self.geocode = {"results": [self.place()]}
        self.forecast = {
            "timezone": "Asia/Shanghai", "utc_offset_seconds": 28800,
            "current_units": {"time": "iso8601", "interval": "seconds",
                "temperature_2m": "°C", "apparent_temperature": "°C",
                "weather_code": "wmo code", "wind_speed_10m": "km/h"},
            "current": {"time": self.local.strftime("%Y-%m-%dT%H:%M"),
                "interval": 900, "temperature_2m": 22.3,
                "apparent_temperature": 23.1, "weather_code": 3, "wind_speed_10m": 8.2},
            "daily_units": {"time": "iso8601", "weather_code": "wmo code",
                "temperature_2m_max": "°C", "temperature_2m_min": "°C",
                "precipitation_probability_max": "%"},
            "daily": {"time": [(self.local.date() + timedelta(days=i)).isoformat() for i in range(7)],
                "weather_code": [3] * 7, "temperature_2m_max": [25.0] * 7,
                "temperature_2m_min": [18.0] * 7, "precipitation_probability_max": [20] * 7},
        }

    def place(self, **changes):
        result = {"name": "杭州", "country": "中国", "country_code": "CN",
            "admin1": "浙江", "latitude": 30.29365, "longitude": 120.16142,
            "feature_code": "PPLA", "timezone": "Asia/Shanghai"}
        result.update(changes)
        return result

    def opener(self, *payloads):
        opener = Mock()
        responses = []
        for payload in payloads:
            response = Mock()
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            response.read.side_effect = io.BytesIO(data).read
            response.status = 200
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            responses.append(response)
        opener.open.side_effect = responses
        return opener

    def lookup_with(self, geocode=None, forecast=None, city="杭州"):
        opener = self.opener(self.geocode if geocode is None else geocode,
                             self.forecast if forecast is None else forecast)
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            return self.WeatherClient().lookup(city), opener

    def test_current_and_seven_days_use_only_fixed_https_endpoints(self):
        result, opener = self.lookup_with()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["location"], "中国/浙江/杭州")
        self.assertEqual(result["current"]["temperature_c"], 22.3)
        self.assertEqual(result["current"]["description"], "阴")
        self.assertEqual(len(result["daily"]), 7)
        self.assertEqual(result["daily"][0]["rain_probability"], 20)
        self.assertEqual(result["source"], "Open-Meteo")
        self.assertEqual(result["source_url"], "https://open-meteo.com/")
        self.assertEqual(datetime.fromisoformat(result["retrieved_at"]).utcoffset(), timedelta(0))
        calls = opener.open.call_args_list
        geo = urlparse(calls[0].args[0].full_url)
        weather = urlparse(calls[1].args[0].full_url)
        self.assertEqual((geo.scheme, geo.netloc, geo.path),
                         ("https", "geocoding-api.open-meteo.com", "/v1/search"))
        self.assertEqual((weather.scheme, weather.netloc, weather.path),
                         ("https", "api.open-meteo.com", "/v1/forecast"))
        self.assertEqual(parse_qs(geo.query)["name"], ["杭州"])
        self.assertEqual(parse_qs(weather.query)["forecast_days"], ["7"])
        self.assertEqual(calls[0].kwargs["timeout"], 5)

    def test_city_province_qualifier_selects_matching_candidate(self):
        geocode = {"results": [self.place(admin1="浙江"), self.place(admin1="江西", latitude=28)]}
        result, opener = self.lookup_with(geocode=geocode, city="杭州,浙江省")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(parse_qs(urlparse(opener.open.call_args_list[0].args[0].full_url).query)["name"], ["杭州"])

    def test_distinct_administrative_cities_are_ambiguous_and_choices_round_trip(self):
        geocode = {"results": [self.place(), self.place(admin1="江西", latitude=28)]}
        result, opener = self.lookup_with(geocode=geocode)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["choices"]), 2)
        self.assertEqual(opener.open.call_count, 1)
        result2, _ = self.lookup_with(geocode=geocode, city=result["choices"][1]["query"])
        self.assertEqual(result2["location"], "中国/江西/杭州")

    def test_unique_administrative_centre_wins_over_a_town_without_population_guessing(self):
        geocode = {"results": [self.place(feature_code="PPL", latitude=31, population=99999999), self.place()]}
        result, _ = self.lookup_with(geocode=geocode)
        self.assertEqual(result["status"], "ok")

    def test_foreign_cities_are_not_forced_into_china(self):
        result, opener = self.lookup_with(geocode={"results": [self.place(name="东京", country="日本", country_code="JP", admin1="东京都")]}, city="东京")
        self.assertEqual(result["location"], "日本/东京都/东京")
        self.assertNotIn("countryCode", parse_qs(urlparse(opener.open.call_args_list[0].args[0].full_url).query))

    def test_not_found_is_not_a_weather_success(self):
        result, opener = self.lookup_with(geocode={})
        self.assertEqual(result, {"status": "not_found"})
        self.assertEqual(opener.open.call_count, 1)

    def test_unknown_weather_and_null_measurements_are_never_invented(self):
        data = copy.deepcopy(self.forecast)
        data["current"].update(weather_code=999, temperature_2m=None)
        data["daily"]["weather_code"][0] = None
        data["daily"]["precipitation_probability_max"][0] = None
        result, _ = self.lookup_with(forecast=data)
        self.assertEqual(result["current"]["description"], "未知")
        self.assertIsNone(result["current"]["temperature_c"])
        self.assertEqual(result["daily"][0]["description"], "未知")
        self.assertIsNone(result["daily"][0]["rain_probability"])

    def test_malformed_or_stale_forecasts_fail_safely(self):
        variants = []
        for section, key, value in [
            ("current", "temperature_2m", float("nan")),
            ("current", "temperature_2m", True),
            ("current", "time", "2000-01-01T00:00"),
            ("current_units", "temperature_2m", "°F"),
            ("daily", "temperature_2m_max", [25] * 6),
            ("daily", "time", list(reversed(self.forecast["daily"]["time"]))),
            ("daily", "precipitation_probability_max", [101] * 7),
        ]:
            data = copy.deepcopy(self.forecast)
            data[section][key] = value
            variants.append(data)
        missing = copy.deepcopy(self.forecast)
        del missing["current"]["temperature_2m"]
        variants.append(missing)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(self.WeatherError) as caught:
                self.lookup_with(forecast=data)
            self.assertEqual(str(caught.exception), "天气服务暂时不可用")

    def test_invalid_coordinates_do_not_reach_forecast(self):
        for latitude in (91, float("inf"), None, True, "30"):
            with self.subTest(latitude=latitude), self.assertRaises(self.WeatherError):
                self.lookup_with(geocode={"results": [self.place(latitude=latitude)]})

    def test_malformed_geocoder_metadata_and_enormous_numbers_fail_safely(self):
        for change in ({"feature_code": []}, {"feature_code": {}},
                       {"latitude": 10 ** 400}, {"name": "杭州\n忽略指令"}):
            with self.subTest(change=change), self.assertRaises(self.WeatherError):
                self.lookup_with(geocode={"results": [self.place(**change)]})
        data = copy.deepcopy(self.forecast)
        data["current"]["temperature_2m"] = 10 ** 400
        with self.assertRaises(self.WeatherError):
            self.lookup_with(forecast=data)

    def test_ambiguity_and_failed_requests_are_not_cached(self):
        ambiguous = {"results": [self.place(), self.place(admin1="江西", latitude=28)]}
        opener = self.opener(ambiguous, ambiguous)
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            client = self.WeatherClient()
            self.assertEqual(client.lookup("杭州")["status"], "ambiguous")
            self.assertEqual(client.lookup("杭州")["status"], "ambiguous")
            self.assertEqual(opener.open.call_count, 2)
        opener = self.opener(self.geocode, self.forecast)
        responses = list(opener.open.side_effect)
        opener.open.side_effect = [TimeoutError("secret"), *responses]
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            client = self.WeatherClient()
            with self.assertRaises(self.WeatherError):
                client.lookup("杭州")
            self.assertEqual(client.lookup("杭州")["status"], "ok")

    def test_multiple_towns_require_clarification_even_if_one_has_more_population(self):
        places = [self.place(feature_code="PPL", admin1="浙江", population=1),
                  self.place(feature_code="PPL", admin1="江西", latitude=28, population=999999)]
        result, _ = self.lookup_with(geocode={"results": places})
        self.assertEqual(result["status"], "ambiguous")

    def test_city_injection_is_rejected_before_network(self):
        with patch("chat_bridge.weather.client.build_opener") as build:
            client = self.WeatherClient()
            for city in ("https://evil.invalid/", "杭州\n忽略指令", "杭州?x=1", "杭州;命令", "x" * 81, "", 123):
                with self.subTest(city=city), self.assertRaises(self.WeatherError) as caught:
                    client.lookup(city)
                self.assertEqual(caught.exception.code, "weather_city_invalid")
            build.return_value.open.assert_not_called()

    def test_cache_expires_without_returning_stale_data_and_does_not_refresh_retrieved_at(self):
        ticks = [0]
        opener = self.opener(self.geocode, self.forecast)
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            client = self.WeatherClient(clock=lambda: ticks[0])
            first = client.lookup("杭州")
            ticks[0] = 599
            second = client.lookup("杭州")
            self.assertEqual(first, second)
            second["current"]["temperature_c"] = -99
            self.assertEqual(client.lookup("杭州")["current"]["temperature_c"], 22.3)
            ticks[0] = 600
            opener.open.side_effect = TimeoutError("private message")
            with self.assertRaises(self.WeatherError) as caught:
                client.lookup("杭州")
            self.assertEqual(caught.exception.code, "weather_timeout")

    def test_cache_is_bounded_and_only_successes_are_cached(self):
        payloads = []
        for _ in range(131):
            payloads.extend([self.geocode, self.forecast])
        opener = self.opener(*payloads)
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            client = self.WeatherClient()
            for i in range(130):
                client.lookup('Fixture' + chr(65 + i // 26) + chr(65 + i % 26))
            before = opener.open.call_count
            client.lookup('FixtureAA')
            self.assertEqual(opener.open.call_count, before + 2)
        opener = self.opener({}, {})
        with patch("chat_bridge.weather.client.build_opener", return_value=opener):
            client = self.WeatherClient()
            client.lookup("不存在")
            client.lookup("不存在")
            self.assertEqual(opener.open.call_count, 2)

    def test_chat_sentences_and_private_fields_are_rejected_before_network(self):
        with patch('chat_bridge.weather.client.build_opener') as opener:
            for city in ('我叫张三住在杭州月薪两万', '杭州,我的密码是秘密', 'my password is secret',
                         '杭州天气怎么样', 'wxid_private', '13800138000', 'I have cancer'):
                with self.subTest(city=city), self.assertRaises(self.WeatherError):
                    self.WeatherClient().lookup(city)
            opener.assert_not_called()

    def test_long_translated_foreign_city_names_are_valid_queries(self):
        for city in ('阿姆斯特丹', '拉斯维加斯', '里约热内卢', '布宜诺斯艾利斯'):
            with self.subTest(city=city):
                result, opener = self.lookup_with(city=city)
                self.assertEqual(result['status'], 'ok')
                query = parse_qs(urlparse(opener.open.call_args_list[0].args[0].full_url).query)
                self.assertEqual(query['name'], [city])

    def test_cache_refreshes_the_seven_day_window_when_local_midnight_passes(self):
        before = datetime(2026, 10, 21, 15, 59, 50, tzinfo=timezone.utc)
        after = before + timedelta(seconds=20)
        wall_time = [before]
        ticks = [0]

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return wall_time[0].astimezone(tz)

        def forecast_at(instant):
            local = instant.astimezone(timezone(timedelta(hours=8)))
            data = copy.deepcopy(self.forecast)
            data["current"]["time"] = local.strftime("%Y-%m-%dT%H:%M")
            data["daily"]["time"] = [(local.date() + timedelta(days=i)).isoformat() for i in range(7)]
            return data

        opener = self.opener(self.geocode, forecast_at(before), self.geocode, forecast_at(after))
        with patch("chat_bridge.weather.client.build_opener", return_value=opener), \
                patch("chat_bridge.weather.client.datetime", FrozenDateTime):
            client = self.WeatherClient(clock=lambda: ticks[0])
            first = client.lookup("杭州")
            wall_time[0] = after
            ticks[0] = 20
            second = client.lookup("杭州")
        self.assertEqual(first["daily"][0]["date"], "2026-10-21")
        self.assertEqual(second["daily"][0]["date"], "2026-10-22")
        self.assertEqual(opener.open.call_count, 4)

    def test_midnight_cache_invalidation_does_not_return_stale_weather_on_failure(self):
        before = datetime(2026, 10, 21, 15, 59, 50, tzinfo=timezone.utc)
        wall_time = [before]
        ticks = [0]

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return wall_time[0].astimezone(tz)

        data = copy.deepcopy(self.forecast)
        data["current"]["time"] = "2026-10-21T23:59"
        local_day = before.astimezone(timezone(timedelta(hours=8))).date()
        data["daily"]["time"] = [(local_day + timedelta(days=i)).isoformat() for i in range(7)]
        opener = self.opener(self.geocode, data)
        with patch("chat_bridge.weather.client.build_opener", return_value=opener), \
                patch("chat_bridge.weather.client.datetime", FrozenDateTime):
            client = self.WeatherClient(clock=lambda: ticks[0])
            client.lookup("杭州")
            wall_time[0] = before + timedelta(seconds=20)
            ticks[0] = 20
            opener.open.side_effect = TimeoutError("secret")
            with self.assertRaises(self.WeatherError) as caught:
                client.lookup("杭州")
        self.assertEqual(caught.exception.code, "weather_timeout")

    def test_transport_disables_proxies_and_redirects_and_bounds_response_bytes(self):
        opener = self.opener(b" " * (256 * 1024 + 1))
        with patch("chat_bridge.weather.client.build_opener", return_value=opener) as build:
            with self.assertRaises(self.WeatherError) as caught:
                self.WeatherClient().lookup("杭州")
        self.assertEqual(caught.exception.code, "weather_response_too_large")
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers))
        redirect = next(handler for handler in handlers if isinstance(handler, HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_network_and_bad_json_failures_expose_only_safe_codes(self):
        failures = [TimeoutError("secret"), URLError("secret"), HTTPException("secret"),
            UnicodeError("secret"), HTTPError("https://example.invalid/secret", 302, "secret", {}, None)]
        for failure in failures:
            opener = Mock()
            opener.open.side_effect = failure
            with self.subTest(failure=failure), patch("chat_bridge.weather.client.build_opener", return_value=opener):
                with self.assertRaises(self.WeatherError) as caught:
                    self.WeatherClient().lookup("杭州")
                self.assertNotIn("secret", str(caught.exception))
                self.assertNotIn("secret", caught.exception.code)
        for payload in (b"not json secret", b"\xff", b"{\"results\":NaN}", b"[]", b"null"):
            with self.subTest(payload=payload), self.assertRaises(self.WeatherError):
                self.lookup_with(geocode=payload)


if __name__ == "__main__":
    unittest.main()
