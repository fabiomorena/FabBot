"""
bot/heartbeat_scheduler.py – Phase 145 (Issue #92), erweitert Phase 183 (Issue #108)

Stündlicher Heartbeat: evaluiert Trigger, sendet proaktive Nachrichten
mit Cooldown-Schutz (max. 1 Nachricht / 6h).

Trigger-Priorität:
  1. Time-Trigger (Fälligkeit in N Tagen)
  2. Relationship-Alert (Entität seit >14/30 Tagen nicht erwähnt)
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


async def _send_proactive(bot, chat_id: int, trigger: dict) -> None:
    """Generiert und sendet eine proaktive Nachricht, setzt Cooldown, updated State."""
    message = await generate_proactive_message(trigger)
    await bot.send_message(chat_id=chat_id, text=message)
    set_cooldown()
    try:
        from agent.supervisor import get_graph
        from langchain_core.messages import AIMessage

        config = {"configurable": {"thread_id": str(chat_id)}}
        await get_graph().aupdate_state(
            config,
            {"messages": [AIMessage(content=message)]},
            as_node="supervisor",
        )
    except Exception as state_err:
        logger.warning(f"Heartbeat state update fehlgeschlagen (nicht kritisch): {state_err}")
    name = trigger.get("name", "?")
    if trigger.get("trigger_type") == "relationship_alert":
        logger.info(f"Relationship-Alert gesendet: {name} (seit {trigger.get('days_since_mention')}d nicht erwähnt)")
    else:
        logger.info(f"Proaktive Nachricht gesendet: {name} ({trigger.get('days_until_due')}d)")


async def _run_heartbeat(bot, chat_id: int) -> None:
    # Issue #102: API-Health-Check läuft immer, unabhängig von Cooldown/Mute
    await run_api_health_check(bot, chat_id)

    if is_on_cooldown() or is_muted():
        return

    pending = get_pending_items(limit=20)
    triggered = evaluate_time_triggers(pending)
    if triggered:
        trigger = min(triggered, key=lambda x: x.get("days_until_due", 99))
        await _send_proactive(bot, chat_id, trigger)
        return

    try:
        from agent.proactive.relationship_alert import find_unmentioned_entities, mark_alerted

        alerts = find_unmentioned_entities()
        if alerts:
            await _send_proactive(bot, chat_id, alerts[0])
            mark_alerted(alerts[0]["id"])
    except Exception as e:
        logger.warning(f"Relationship-Alert Fehler (non-critical): {e}")


async def run_heartbeat_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und prüft stündlich auf Trigger."""
    logger.info("Heartbeat Scheduler gestartet – stündlich")
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await _run_heartbeat(bot, chat_id)
        except Exception as e:
            logger.error(f"Heartbeat Fehler: {e}")
