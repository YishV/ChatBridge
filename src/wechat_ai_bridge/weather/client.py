"""Bounded, read-only Open-Meteo geocoding and weather requests."""

import copy
import json
import math
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from wechat_ai_bridge.weather.locations import city_query


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_CACHE_ENTRIES = 128
_CODES = frozenset({"weather_city_invalid", "weather_timeout", "weather_network_error",
    "weather_http_error", "weather_response_invalid", "weather_response_json_invalid",
    "weather_response_too_large"})
_DESCRIPTIONS = {
    0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨", 56: "轻冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "轻冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}


class WeatherError(Exception):
    """A safe failure code, without provider bodies, URLs, or user content."""

    def __init__(self, code="weather_network_error"):
        super().__init__("天气服务暂时不可用")
        self.code = code if isinstance(code, str) and code in _CODES else "weather_network_error"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _city_text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise WeatherError("weather_city_invalid")
    if any(not (char.isalpha() or char.isdecimal() or unicodedata.category(char).startswith("M")
                or char in " ,，-.·'’") for char in value):
        raise WeatherError("weather_city_invalid")
    normalized = " ".join(value.replace("，", ",").split())
    parts = [part.strip() for part in normalized.split(",")]
    if len(parts) > 6 or any(not part or not any(char.isalpha() for char in part) for part in parts):
        raise WeatherError("weather_city_invalid")
    return ",".join(parts)


def _place_text(value):
    try:
        result = _city_text(value)
    except WeatherError:
        raise WeatherError("weather_response_invalid") from None
    # Commas are query separators; remote administrative labels must be single components.
    if "," in result:
        raise WeatherError("weather_response_invalid")
    return result


def _region_key(value):
    value = value.casefold().replace(" ", "")
    return re.sub(r"(?:特别行政区|维吾尔自治区|壮族自治区|回族自治区|自治区|自治州|省|市)$", "", value)


def _number(value, low, high, *, optional=False):
    if optional and value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not low <= value <= high or not math.isfinite(value)):
        raise WeatherError("weather_response_invalid")
    return value


def _description(code):
    if code is None:
        return "未知"
    if isinstance(code, bool) or not isinstance(code, int) or code < 0:
        raise WeatherError("weather_response_invalid")
    return _DESCRIPTIONS.get(code, "未知")


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON number")


class WeatherClient:
    def __init__(self, timeout_seconds=5, cache_seconds=600, clock=time.monotonic):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30):
            raise ValueError("天气请求超时必须在 0 至 30 秒之间")
        if (isinstance(cache_seconds, bool) or not isinstance(cache_seconds, (int, float))
                or not math.isfinite(cache_seconds) or not 0 <= cache_seconds <= 3600):
            raise ValueError("天气缓存时间必须在 0 至 3600 秒之间")
        if not callable(clock):
            raise ValueError("天气缓存时钟必须可调用")
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._cache = OrderedDict()
        self._cache_lock = Lock()

    def _request(self, endpoint, params):
        # Endpoint constants are private and never derived from the query or provider data.
        if endpoint not in (_GEOCODING_URL, _FORECAST_URL):
            raise WeatherError("weather_network_error")
        request = Request(endpoint + "?" + urlencode(params), headers={
            "Accept": "application/json", "User-Agent": "wechat-ai-bridge-weather/1.0"})
        try:
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise WeatherError("weather_http_error")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise WeatherError("weather_response_too_large")
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
            if not isinstance(data, dict) or data.get("error"):
                raise WeatherError("weather_response_invalid")
            return data
        except WeatherError:
            raise
        except (TimeoutError,):
            raise WeatherError("weather_timeout") from None
        except HTTPError:
            raise WeatherError("weather_http_error") from None
        except URLError as exc:
            code = "weather_timeout" if isinstance(exc.reason, TimeoutError) else "weather_network_error"
            raise WeatherError(code) from None
        except (OSError, HTTPException):
            raise WeatherError("weather_network_error") from None
        except (UnicodeError, ValueError, RecursionError):
            raise WeatherError("weather_response_json_invalid") from None

    def _locations(self, city):
        name, *qualifiers = city.split(",")
        data = self._request(_GEOCODING_URL, {
            "name": name, "language": "zh", "count": 10, "format": "json"})
        results = data.get("results", [])
        if not isinstance(results, list) or len(results) > 10:
            raise WeatherError("weather_response_invalid")
        locations = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                raise WeatherError("weather_response_invalid")
            latitude = _number(item.get("latitude"), -90, 90)
            longitude = _number(item.get("longitude"), -180, 180)
            country = _place_text(item.get("country"))
            place = _place_text(item.get("name"))
            feature_code = item.get("feature_code")
            if feature_code is not None and not isinstance(feature_code, str):
                raise WeatherError("weather_response_invalid")
            regions = [_place_text(item[key]) for key in ("admin1", "admin2", "admin3", "admin4")
                       if item.get(key)]
            components = list(dict.fromkeys([country, *regions, place]))
            keys = {_region_key(part) for part in components}
            if isinstance(item.get("country_code"), str):
                keys.add(item["country_code"].casefold())
            if any(_region_key(part) not in keys for part in qualifiers):
                continue
            identity = (tuple(components), latitude, longitude)
            if identity in seen:
                continue
            seen.add(identity)
            # A reply choice can be fed straight back through the same city validator.
            query = ",".join(dict.fromkeys([place, *reversed(regions), country]))
            try:
                query = _city_text(query)
            except WeatherError:
                raise WeatherError("weather_response_invalid") from None
            locations.append({"label": "/".join(components), "query": query,
                "latitude": latitude, "longitude": longitude,
                "feature_code": feature_code})
        centres = [item for item in locations if item["feature_code"] in {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4"}]
        # An administrative centre may outrank a small same-name locality, never another centre.
        return centres if centres else locations

    def _forecast(self, location):
        data = self._request(_FORECAST_URL, {
            "latitude": location["latitude"], "longitude": location["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 7,
            "temperature_unit": "celsius", "wind_speed_unit": "kmh"})
        try:
            tz_name = data["timezone"]
            if not isinstance(tz_name, str) or not re.fullmatch(r"[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*", tz_name):
                raise WeatherError("weather_response_invalid")
            offset = data["utc_offset_seconds"]
            if isinstance(offset, bool) or not isinstance(offset, int) or not -43200 <= offset <= 50400:
                raise WeatherError("weather_response_invalid")
            current = data["current"]
            daily = data["daily"]
            current_units = data["current_units"]
            daily_units = data["daily_units"]
            if not all(isinstance(item, dict) for item in (current, daily, current_units, daily_units)):
                raise WeatherError("weather_response_invalid")
            for key, unit in {"time": "iso8601", "temperature_2m": "°C", "apparent_temperature": "°C",
                              "weather_code": "wmo code", "wind_speed_10m": "km/h"}.items():
                if current_units[key] != unit:
                    raise WeatherError("weather_response_invalid")
            for key, unit in {"time": "iso8601", "weather_code": "wmo code", "temperature_2m_max": "°C",
                              "temperature_2m_min": "°C", "precipitation_probability_max": "%"}.items():
                if daily_units[key] != unit:
                    raise WeatherError("weather_response_invalid")
            now = datetime.now(timezone.utc)
            local_zone = timezone(timedelta(seconds=offset))
            timestamp = current["time"]
            if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", timestamp):
                raise WeatherError("weather_response_invalid")
            measured_at = datetime.fromisoformat(timestamp).replace(tzinfo=local_zone)
            age = (now - measured_at).total_seconds()
            if not -3600 <= age <= 10800:
                raise WeatherError("weather_response_invalid")
            output_current = {"time": timestamp,
                "temperature_c": _number(current["temperature_2m"], -150, 80, optional=True),
                "apparent_temperature_c": _number(current["apparent_temperature"], -200, 100, optional=True),
                "description": _description(current["weather_code"]),
                "wind_speed_kmh": _number(current["wind_speed_10m"], 0, 500, optional=True)}
            fields = ("time", "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max")
            if any(not isinstance(daily[key], list) or len(daily[key]) != 7 for key in fields):
                raise WeatherError("weather_response_invalid")
            today = now.astimezone(local_zone).date()
            output_daily = []
            for index in range(7):
                day = daily["time"][index]
                if not isinstance(day, str) or day != (today + timedelta(days=index)).isoformat():
                    raise WeatherError("weather_response_invalid")
                date.fromisoformat(day)
                low = _number(daily["temperature_2m_min"][index], -150, 80, optional=True)
                high = _number(daily["temperature_2m_max"][index], -150, 80, optional=True)
                if low is not None and high is not None and low > high:
                    raise WeatherError("weather_response_invalid")
                output_daily.append({"date": day, "description": _description(daily["weather_code"][index]),
                    "temperature_min_c": low, "temperature_max_c": high,
                    "rain_probability": _number(daily["precipitation_probability_max"][index], 0, 100, optional=True)})
            return {"status": "ok", "location": location["label"], "current": output_current,
                "daily": output_daily, "timezone": tz_name, "utc_offset_seconds": offset,
                "retrieved_at": now.isoformat(),
                "source": "Open-Meteo", "source_url": "https://open-meteo.com/"}
        except (KeyError, TypeError, ValueError, OverflowError):
            raise WeatherError("weather_response_invalid") from None

    def lookup(self, city: str) -> dict:
        try:
            city = _city_text(city_query(city))
        except ValueError:
            raise WeatherError('weather_city_invalid') from None
        key = city.casefold()
        now = self._clock()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                expires, result = cached
                local_zone = timezone(timedelta(seconds=result["utc_offset_seconds"]))
                local_today = datetime.now(timezone.utc).astimezone(local_zone).date().isoformat()
                if now < expires and result["daily"][0]["date"] == local_today:
                    self._cache.move_to_end(key)
                    return copy.deepcopy(result)
                del self._cache[key]
        locations = self._locations(city)
        if not locations:
            return {"status": "not_found"}
        if len(locations) > 1:
            return {"status": "ambiguous", "choices": [
                {"label": item["label"], "query": item["query"]} for item in locations]}
        result = self._forecast(locations[0])
        if self.cache_seconds:
            with self._cache_lock:
                self._cache[key] = (self._clock() + self.cache_seconds, copy.deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > _MAX_CACHE_ENTRIES:
                    self._cache.popitem(last=False)
        return result
