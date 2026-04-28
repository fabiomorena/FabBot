"""tests/test_issue_116_weather_short_hourly.py – Issue #116

Regression: ``_get_weather`` used to read ``forecast["hourly"][4]``
unconditionally. When wttr.in returned fewer than 5 hourly slots the
bare index raised ``IndexError``, which the surrounding
``except Exception`` swallowed and replaced with an empty string so the
user got a blank reply. The fix bounds-checks ``hourly`` before reading
slot 4 and falls back to ``"?"`` for the description, returning a
partial forecast instead of nothing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _wttr_response(*, n_hourly: int) -> MagicMock:
    """Build a minimal wttr.in JSON-shape response for ``_get_weather``.

    The function looks up ``data["weather"][day_idx]`` and from there
    ``maxtempC``, ``mintempC``, and ``hourly[4].weatherDesc[0].value``.
    Only those keys need to be present for the day-1 (morgen) branch.
    """
    return {
        "weather": [
            {  # day 0 — today
                "maxtempC": "20",
                "mintempC": "10",
                "hourly": [{"weatherDesc": [{"value": "Sunny"}]}] * 8,
            },
            {  # day 1 — tomorrow, the short-hourly case
                "maxtempC": "22",
                "mintempC": "11",
                "hourly": [
                    {"weatherDesc": [{"value": "Cloudy"}]}
                ] * n_hourly,
            },
        ],
        "current_condition": [
            {
                "weatherDesc": [{"value": "Sunny"}],
                "temp_C": "15",
                "FeelsLikeC": "14",
                "humidity": "55",
                "windspeedKmph": "10",
            }
        ],
    }


@pytest.mark.asyncio
async def test_short_hourly_does_not_swallow_partial_forecast():
    """Issue #116: wttr.in returns 3 hourly slots → user must still
    get the max/min summary, not an empty reply."""
    from agent.agents import web as web_module

    payload = _wttr_response(n_hourly=3)

    with patch("agent.agents.web.load_profile", return_value={
        "identity": {"location": "Berlin"}
    }), patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(return_value=payload)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await web_module._get_weather("morgen")

    # Pre-fix this returned "" because IndexError was swallowed.
    assert result != ""
    assert "Berlin" in result
    assert "Morgen" in result
    # The numeric summary still comes through.
    assert "max 22°C" in result
    assert "min 11°C" in result


@pytest.mark.asyncio
async def test_full_hourly_still_uses_slot_4_description():
    """Sanity: the happy path (8 hourly slots) still picks the
    weatherDesc from slot 4 — the fix is bounds-only, not behavioural."""
    from agent.agents import web as web_module

    payload = _wttr_response(n_hourly=8)
    # Distinguish slot 4's description so we can assert it ends up
    # in the user-facing reply.
    payload["weather"][1]["hourly"][4] = {
        "weatherDesc": [{"value": "Partly cloudy"}]
    }

    with patch("agent.agents.web.load_profile", return_value={
        "identity": {"location": "Berlin"}
    }), patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(return_value=payload)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await web_module._get_weather("morgen")

    assert "Partly cloudy" in result
