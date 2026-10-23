"""Small, validated plan contract; no executable URLs or free-form commands."""

import re
from datetime import date


def validate_plan(data) -> dict:
    if not isinstance(data, dict) or set(data) != {'action', 'city', 'start_date', 'days'}:
        raise ValueError('weather_plan_invalid')
    if data['action'] not in ('query', 'clarify', 'none', 'unsupported'):
        raise ValueError('weather_plan_invalid')
    city = data['city']
    if (not isinstance(city, str) or len(city) > 80 or city != city.strip()
            or city and not re.fullmatch(r"[\w ,，.'’·()（）\-]+", city)
            or data['action'] == 'query' and not city):
        raise ValueError('weather_plan_invalid')
    start = data['start_date']
    if not isinstance(start, str) or start and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', start):
        raise ValueError('weather_plan_invalid')
    if start:
        date.fromisoformat(start)
    if type(data['days']) is not int or not 1 <= data['days'] <= 7:
        raise ValueError('weather_plan_invalid')
    return dict(data)
