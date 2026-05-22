"""
tests/test_ph215_briefing_fixes.py – Phase 215

Testet die drei Briefing-Fixes:
- Wetter: Open-Meteo statt wttr.in (_WMO_DESCRIPTIONS, _OPEN_METEO_URL)
- News-Timeout: 30s LLM / 45s Orchestrator
- Party Report: _extract_next_data_events parst __NEXT_DATA__ JSON
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Wetter: Open-Meteo
# ---------------------------------------------------------------------------


class TestOpenMeteo:
    def test_wmo_descriptions_has_common_codes(self):
        from bot.briefing import _WMO_DESCRIPTIONS

        assert 0 in _WMO_DESCRIPTIONS  # Clear sky
        assert 3 in _WMO_DESCRIPTIONS  # Overcast
        assert 61 in _WMO_DESCRIPTIONS  # Rain
        assert 95 in _WMO_DESCRIPTIONS  # Thunderstorm

    def test_open_meteo_url_contains_berlin_coords(self):
        from bot.briefing import _OPEN_METEO_URL

        assert "52.52" in _OPEN_METEO_URL
        assert "13.41" in _OPEN_METEO_URL
        assert "relative_humidity_2m" in _OPEN_METEO_URL

    @pytest.mark.asyncio
    async def test_get_weather_formats_correctly(self):
        from bot.briefing import _get_weather_berlin

        mock_data = {
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
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = mock_data

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await _get_weather_berlin()

        assert "65%" in result
        assert "18°C" in result
        assert "11°C" in result
        assert "22°C" in result
        assert "12 km/h" in result

    @pytest.mark.asyncio
    async def test_get_weather_fallback_on_error(self):
        from bot.briefing import _get_weather_berlin

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("offline"))
            mock_client_cls.return_value = mock_client

            result = await _get_weather_berlin()

        assert "nicht verfügbar" in result


# ---------------------------------------------------------------------------
# News-Timeout
# ---------------------------------------------------------------------------


class TestNewsTimeout:
    def test_orchestrator_news_timeout_is_45(self):
        from agent.proactive.briefing_agent import _TIMEOUTS

        assert _TIMEOUTS["news"] == 45.0

    @pytest.mark.asyncio
    async def test_format_news_timeout_is_30(self):
        """Haiku-Timeout in _format_news_with_llm muss >= 30s sein."""
        import inspect
        from bot.briefing import _format_news_with_llm

        source = inspect.getsource(_format_news_with_llm)
        assert "timeout=30" in source


# ---------------------------------------------------------------------------
# Party Report: __NEXT_DATA__ Extraktion
# ---------------------------------------------------------------------------


class TestExtractNextDataEvents:
    def _make_html(self, next_data: dict) -> str:
        payload = json.dumps(next_data)
        return f'<html><head><script id="__NEXT_DATA__" type="application/json">{payload}</script></head></html>'

    def test_extracts_events_from_known_path(self):
        from bot.party_report import _extract_next_data_events

        data = {
            "props": {
                "pageProps": {
                    "venue": {
                        "events": [
                            {
                                "title": "Techno Night",
                                "startTime": "2026-05-22T22:00:00Z",
                                "contentUrl": "https://ra.co/events/12345",
                                "artists": [{"name": "DJ Fabio"}],
                            }
                        ]
                    }
                }
            }
        }
        html = self._make_html(data)
        result = _extract_next_data_events(html, "berghain")
        assert "Techno Night" in result
        assert "DJ Fabio" in result

    def test_falls_back_to_compact_json_when_path_unknown(self):
        from bot.party_report import _extract_next_data_events

        data = {"something": {"else": [{"event": "Unknown structure"}]}}
        html = self._make_html(data)
        result = _extract_next_data_events(html, "testclub")
        assert len(result) > 0
        assert "Unknown structure" in result

    def test_returns_empty_when_no_next_data(self):
        from bot.party_report import _extract_next_data_events

        html = "<html><body>Plain HTML, no Next.js</body></html>"
        result = _extract_next_data_events(html, "testclub")
        assert result == ""

    def test_returns_empty_on_invalid_json(self):
        from bot.party_report import _extract_next_data_events

        html = '<script id="__NEXT_DATA__" type="application/json">{invalid json}</script>'
        result = _extract_next_data_events(html, "testclub")
        assert result == ""

    def test_limits_to_10_events(self):
        from bot.party_report import _extract_next_data_events

        events = [{"title": f"Event {i}", "startTime": "2026-05-22T22:00:00Z"} for i in range(15)]
        data = {"props": {"pageProps": {"venue": {"events": events}}}}
        html = self._make_html(data)
        result = _extract_next_data_events(html, "testclub")
        # Header-Zeile "RA Events für testclub:" zählt auch → max 11 Treffer (1 Header + 10 Events)
        assert result.count("Event") <= 11
        assert result.count("\n") <= 10  # max 10 Event-Zeilen nach Header
