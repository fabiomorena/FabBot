"""
agent/proactive/api_health.py – Issue #102

Leichtgewichtiger API-Health-Check für den stündlichen Heartbeat.
Prüft via HEAD-Request ob externe APIs erreichbar sind.
Alertet nur bei Zustandsänderung (up→down, down→up).

Geprüfte APIs:
- Anthropic (immer)
- Tavily (wenn TAVILY_API_KEY gesetzt)
- Brave  (wenn BRAVE_API_KEY gesetzt)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_STATE_FILE = Path.home() / ".fabbot" / "api_health_state.json"
_TIMEOUT = 8.0

_API_ENDPOINTS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "tavily": "https://api.tavily.com",
    "brave": "https://api.search.brave.com",
}

_API_LABELS: dict[str, str] = {
    "anthropic": "Anthropic API",
    "tavily": "Tavily Search",
    "brave": "Brave Search",
}


def _active_apis() -> list[str]:
    """Gibt APIs zurück die geprüft werden sollen (Anthropic immer, Rest nur mit Key)."""
    apis = ["anthropic"]
    if os.getenv("TAVILY_API_KEY"):
        apis.append("tavily")
    if os.getenv("BRAVE_API_KEY"):
        apis.append("brave")
    return apis


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    _STATE_FILE.write_text(json.dumps(state))


async def _ping(api_name: str) -> bool:
    """HEAD-Request auf den API-Endpoint. True = erreichbar."""
    url = _API_ENDPOINTS[api_name]
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.head(url)
            # Jeder HTTP-Response (auch 4xx) bedeutet erreichbar
            return resp.status_code < 600
    except Exception:
        return False


async def check_apis() -> dict[str, bool]:
    """Prüft alle aktiven APIs und gibt {name: is_up} zurück."""
    import asyncio

    apis = _active_apis()
    results = await asyncio.gather(*[_ping(api) for api in apis], return_exceptions=True)
    return {api: (result is True) for api, result in zip(apis, results)}


def get_alert_messages(current: dict[str, bool], previous: dict[str, bool]) -> list[str]:
    """
    Vergleicht aktuellen mit vorherigem Zustand.
    Gibt Alert-Nachrichten zurück für APIs die sich geändert haben.
    """
    alerts = []
    for api, is_up in current.items():
        was_up = previous.get(api)
        label = _API_LABELS.get(api, api)
        if was_up is None:
            # Erster Check – kein Alert
            continue
        if was_up and not is_up:
            alerts.append(f"⚠️ {label} nicht erreichbar")
        elif not was_up and is_up:
            alerts.append(f"✅ {label} wieder erreichbar")
    return alerts


async def run_api_health_check(bot, chat_id: int) -> None:
    """
    Führt den API-Health-Check durch.
    Sendet nur eine Nachricht wenn sich der Zustand geändert hat.
    Fail-safe – kein Fehler beeinflusst den Bot-Betrieb.
    """
    try:
        previous = _load_state()
        current = await check_apis()

        alerts = get_alert_messages(current, previous)
        _save_state(current)

        if alerts:
            text = "\n".join(alerts)
            await bot.send_message(chat_id=chat_id, text=text)
            logger.info(f"API-Health-Alert gesendet: {alerts}")
        else:
            logger.debug(f"API-Health-Check OK: {current}")

    except Exception as e:
        logger.error(f"API-Health-Check Fehler (nicht kritisch): {e}")
