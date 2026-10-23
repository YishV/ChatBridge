"""Conservative location-only boundary for outbound geocoding parameters."""

import re
import unicodedata


_PRIVATE_OR_SENTENCE = re.compile(
    r'我叫|我住|我的|我是|我在|我有|我想|我们|住在|月薪|工资|密码|密钥|身份证|手机号|电话号码|微信|群友|成员|'
    r'天气|气温|带伞|下雨|明天|后天|今天|查询|帮我|忽略|指令|'
    r'\b(?:my name|i (?:have|am|live|work|like)|password|salary|phone|secret|api.?key|token|wxid|weather|tomorrow)\b', re.I)


def city_query(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or _PRIVATE_OR_SENTENCE.search(value):
        raise ValueError('weather_city_invalid')
    if any(not (c.isalpha() or unicodedata.category(c).startswith('M') or c in " ,，-.·'’") for c in value):
        raise ValueError('weather_city_invalid')
    parts = [' '.join(part.split()) for part in value.replace('，', ',').split(',')]
    if not 1 <= len(parts) <= 6 or any(not part for part in parts):
        raise ValueError('weather_city_invalid')
    for part in parts:
        if len(part) > 40 or len(part.split()) > 5:
            raise ValueError('weather_city_invalid')
        if re.fullmatch(r'[\u3400-\u9fff·]+', part):
            if len(part) > 20:
                raise ValueError('weather_city_invalid')
    return ','.join(parts)
