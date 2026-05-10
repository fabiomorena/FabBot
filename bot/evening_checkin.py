"""
bot/evening_checkin.py – Phase 195 (Issue #109)

Täglicher Abend-Check-in um 21:00 Uhr (Berlin-Zeit). Sendet Fabio eine persönliche
Frage basierend auf dem heutigen Gesprächsverlauf. Unabhängig vom Proaktiv-Cooldown.

State: ~/.fabbot/evening_checkin_state.json → { "last_sent_date": "YYYY-MM-DD" }

API:
  run_evening_checkin_scheduler(bot, chat_id) → None  (async)
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.config import get_settings

logger = logging.getLogger(__name__)

_TZ_BERLIN = ZoneInfo("Europe/Berlin")
_CHECKIN_STATE_FILE = Path.home() / ".fabbot" / "evening_checkin_state.json"
_LLM_TIMEOUT = 8.0
_FALLBACK_QUESTION = "Wie war dein Tag? Was hat dich heute beschäftigt?"


def _load_state() -> dict:
    try:
        return json.loads(_CHECKIN_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    _CHECKIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CHECKIN_STATE_FILE.write_text(json.dumps(data))


def _already_sent_today() -> bool:
    last_date = _load_state().get("last_sent_date")
    if not last_date:
        return False
    try:
        return date.fromisoformat(last_date) == datetime.now(_TZ_BERLIN).date()
    except ValueError:
        return False


async def _generate_checkin_question(chat_id: int) -> str:
    try:
        from bot.session_summary import _filter_messages, _format_for_summary, _get_messages_from_state
        from agent.llm import get_fast_llm
        from langchain_core.messages import HumanMessage

        messages = await _get_messages_from_state(chat_id)
        filtered = _filter_messages(messages)
        chat_context = _format_for_summary(filtered) if filtered else ""

        prompt = f"""Schreibe eine kurze, persönliche Abend-Frage für Fabio.

=== Heutiger Gesprächsverlauf ===
{chat_context or "(keine Gespräche heute)"}

Regeln:
- 1–2 Sätze
- Direkt und warm, kein "Guten Abend", kein "Hallo"
- Frage über seinen Tag oder ein konkretes Thema aus dem Gesprächsverlauf
- Deutsch, kein Emoji
- Falls kein Verlauf: allgemeine, offene Frage"""

        llm = get_fast_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_TIMEOUT,
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return content.strip() or _FALLBACK_QUESTION
    except Exception as e:
        logger.warning(f"Evening Check-in Generierung fehlgeschlagen: {e}")
        return _FALLBACK_QUESTION


async def run_evening_checkin_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und sendet täglich den Abend-Check-in."""
    settings = get_settings()
    checkin_time = settings.evening_checkin_time
    # hour/minute werden einmalig gecacht; eine Änderung von EVENING_CHECKIN_TIME
    # zur Laufzeit (+ cache_clear()) greift erst nach Bot-Neustart.
    hour, minute = map(int, checkin_time.split(":"))
    logger.info(f"Evening Check-in Scheduler gestartet – täglich um {checkin_time} Uhr")

    while True:
        if not get_settings().evening_checkin_enabled:
            await asyncio.sleep(3600)
            continue

        now = datetime.now(_TZ_BERLIN)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"Nächster Abend-Check-in in {wait_seconds / 3600:.1f} Stunden")
        await asyncio.sleep(wait_seconds)

        if _already_sent_today():
            logger.info("Abend-Check-in: heute bereits gesendet – skip")
            await asyncio.sleep(60)
            continue

        try:
            question = await _generate_checkin_question(chat_id)
            await bot.send_message(chat_id=chat_id, text=question)
            _save_state({"last_sent_date": datetime.now(_TZ_BERLIN).date().isoformat()})

            try:
                from agent.supervisor import get_graph
                from langchain_core.messages import AIMessage

                config = {"configurable": {"thread_id": str(chat_id)}}
                await get_graph().aupdate_state(
                    config,
                    {"messages": [AIMessage(content=question)]},
                    as_node="supervisor",
                )
            except Exception as state_err:
                logger.warning(f"Check-in state update fehlgeschlagen (nicht kritisch): {state_err}")

            logger.info("Abend-Check-in gesendet.")
        except Exception as e:
            logger.error(f"Evening Check-in Fehler: {e}")

        await asyncio.sleep(60)
