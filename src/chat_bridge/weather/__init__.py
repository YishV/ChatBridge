"""Public weather lookup without sending chat content to the provider."""

from .client import WeatherClient, WeatherError

__all__ = ["WeatherClient", "WeatherError"]
