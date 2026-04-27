"""
bot/heartbeat_scheduler.py – Phase 145 (Issue #92)

Stündlicher Heartbeat: evaluiert Trigger, sendet proaktive Nachrichten
mit Cooldown-Schutz (max. 1 Nachricht / 6h).
"""

import asyncio
import logging

from agent.proactive.heartbeat import (
    evaluate_time_triggers,
    generate_proactive_message,
    is_muted,
    is_on_cooldown,
    set_cooldown,
)
from agent.proactive.pending import get_pending_items
from agent.proactive.api_health import run_api_health_check

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 3600  # 1 Stunde


async def _run_heartbeat(bot, chat_id: int) -> None:
    # Issue #102: API-Health-Check läuft immer, unabhängig von Cooldown/Mute
    await run_api_health_check(bot, chat_id)

    if is_on_cooldown() or is_muted():
        return

    pending = get_pending_items(limit=20)
    triggered = evaluate_time_triggers(pending)
    if not triggered:
        return

    trigger = min(triggered, key=lambda x: x.get("days_until_due", 99))
    message = await generate_proactive_message(trigger)

    await bot.send_message(chat_id=chat_id, text=f"💡 {message}")
    set_cooldown()
    logger.info(f"Proaktive Nachricht gesendet: {trigger.get('name')} ({trigger.get('days_until_due')}d)")


async def run_heartbeat_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und prüft stündlich auf Trigger."""
    logger.info("Heartbeat Scheduler gestartet – stündlich")
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await _run_heartbeat(bot, chat_id)
        except Exception as e:
            logger.error(f"Heartbeat Fehler: {e}")
