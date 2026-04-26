"""
agent/proactive/briefing_agent.py – Phase 144 (Issue #91)

Multi-Agent Briefing Orchestrator: führt alle Briefing-Sub-Agenten
parallel aus, mit Timeout und Graceful Fallback pro Agent.

API:
  orchestrate_briefing(weather_fn, calendar_fn, pending_fn, news_fn, timeout) → dict
  _run_with_timeout(coro, fallback, name, timeout) → str
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_TIMEOUTS = {
    "weather": 10.0,
    "calendar": 15.0,
    "pending": 5.0,
    "news": 30.0,
}

_FALLBACKS = {
    "weather": "Wetter nicht verfügbar.",
    "calendar": "Keine Termine heute.",
    "pending": "",
    "news": "Keine News verfügbar.",
}


async def _run_with_timeout(
    coro: Coroutine[Any, Any, str],
    fallback: str,
    name: str,
    timeout: float = 10.0,
) -> str:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Briefing-Agent '{name}' Timeout ({timeout}s) – Fallback")
        return fallback
    except Exception as e:
        logger.warning(f"Briefing-Agent '{name}' Fehler: {e} – Fallback")
        return fallback


async def orchestrate_briefing(
    weather_fn: Callable[[], Coroutine[Any, Any, str]],
    calendar_fn: Callable[[], Coroutine[Any, Any, str]],
    pending_fn: Callable[[], Coroutine[Any, Any, str]],
    news_fn: Callable[[], Coroutine[Any, Any, str]],
    timeout: float | None = None,
) -> dict[str, str]:
    """Führt alle Sub-Agenten parallel aus und gibt Sektions-Dict zurück.

    timeout: wenn gesetzt, überschreibt die sektionsspezifischen Timeouts (nur für Tests).
    """
    t = lambda name: timeout if timeout is not None else _TIMEOUTS[name]  # noqa: E731
    weather, calendar, pending, news = await asyncio.gather(
        _run_with_timeout(weather_fn(), _FALLBACKS["weather"], "weather", t("weather")),
        _run_with_timeout(calendar_fn(), _FALLBACKS["calendar"], "calendar", t("calendar")),
        _run_with_timeout(pending_fn(), _FALLBACKS["pending"], "pending", t("pending")),
        _run_with_timeout(news_fn(), _FALLBACKS["news"], "news", t("news")),
    )
    return {
        "weather": weather,
        "calendar": calendar,
        "pending": pending,
        "news": news,
    }
