"""
tests/test_ph222_weather_retry.py – Phase 222

Wetter-Abruf im Morning Briefing schlug seit Tagen um 07:30 fehl:
Open-Meteo hing/lieferte 502, 10s-Timeout ohne Retry und ohne Fallback-Provider.

Fix:
- _get_weather_berlin: Retry (3 Versuche) über zwei Provider:
  Open-Meteo primär, Brightsky (DWD, kein API-Key) als Fallback
- Orchestrator-Timeout für weather: 45s (Retry-Budget passt rein)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_OPEN_METEO_DATA = {
    "current": {
        "temperature_2m": 18.4,
        "apparent_temperature": 16.1,
        "relative_humidity_2m": 65,
        "weather_code": 2,
        "windspeed_10m": 12.3,
    },
    "daily": {
        "temperature_2m_max": [22.0],
        "temperature_2m_min": [11.0],
    },
}

_BRIGHTSKY_CURRENT = {
    "weather": {
        "temperature": 18.0,
        "relative_humidity": 50,
        "wind_speed_10": 18.7,
        "icon": "cloudy",
    }
}

_BRIGHTSKY_DAILY = {
    "weather": [
        {"temperature": 13.1},
        {"temperature": 22.4},
        {"temperature": None},
    ]
}


def _make_mock_client(get_mock: AsyncMock) -> MagicMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = get_mock
    return mock_client


def _make_resp(data: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = data
    return mock_resp


class TestWeatherRetry:
    def test_orchestrator_weather_timeout_covers_retry_budget(self):
        from agent.proactive.briefing_agent import _TIMEOUTS

        assert _TIMEOUTS["weather"] >= 25.0

    def test_retry_constants(self):
        from bot.briefing import _WEATHER_RETRIES, _WEATHER_RETRY_DELAY

        assert _WEATHER_RETRIES >= 3
        assert _WEATHER_RETRY_DELAY > 0

    @pytest.mark.asyncio
    async def test_brightsky_fallback_when_open_meteo_down(self):
        """Open-Meteo liefert 502 → Brightsky übernimmt im selben Versuch."""
        from bot import briefing

        get_mock = AsyncMock(
            side_effect=[
                Exception("502 Bad Gateway"),
                _make_resp(_BRIGHTSKY_CURRENT),
                _make_resp(_BRIGHTSKY_DAILY),
            ]
        )

        with (
            patch("httpx.AsyncClient", return_value=_make_mock_client(get_mock)),
            patch.object(briefing, "_WEATHER_RETRY_DELAY", 0),
        ):
            result = await briefing._get_weather_berlin()

        assert "18°C" in result
        assert "13°C" in result
        assert "22°C" in result
        assert "50%" in result
        assert "19 km/h" in result
        assert "nicht verfügbar" not in result

    @pytest.mark.asyncio
    async def test_open_meteo_succeeds_on_second_attempt(self):
        """Erster Versuch scheitert komplett (Netz weg), zweiter klappt."""
        from bot import briefing

        get_mock = AsyncMock(
            side_effect=[
                Exception("network down"),  # Open-Meteo, Versuch 1
                Exception("network down"),  # Brightsky, Versuch 1
                _make_resp(_OPEN_METEO_DATA),  # Open-Meteo, Versuch 2
            ]
        )

        with (
            patch("httpx.AsyncClient", return_value=_make_mock_client(get_mock)),
            patch.object(briefing, "_WEATHER_RETRY_DELAY", 0),
        ):
            result = await briefing._get_weather_berlin()

        assert get_mock.call_count == 3
        assert "18°C" in result
        assert "nicht verfügbar" not in result

    @pytest.mark.asyncio
    async def test_weather_fallback_after_all_retries(self):
        from bot import briefing

        get_mock = AsyncMock(side_effect=Exception("offline"))

        with (
            patch("httpx.AsyncClient", return_value=_make_mock_client(get_mock)),
            patch.object(briefing, "_WEATHER_RETRY_DELAY", 0),
        ):
            result = await briefing._get_weather_berlin()

        # Pro Versuch je 1 fehlgeschlagener Call für Open-Meteo und Brightsky
        assert get_mock.call_count == briefing._WEATHER_RETRIES * 2
        assert "nicht verfügbar" in result
