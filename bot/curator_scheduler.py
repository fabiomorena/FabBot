"""
bot/curator_scheduler.py – Phase 181 (Issue #143)

Stündlicher Background-Check: startet Curator-Dry-Run wenn Bedingungen erfüllt.
Analog zu heartbeat_scheduler – sleep zuerst, dann prüfen.
"""

import asyncio
import logging

from agent.proactive.curator import run_dry_run, should_run

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 3600  # 1 Stunde


async def run_curator_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und prüft stündlich ob Curator-Dry-Run fällig ist."""
    logger.info("Curator Scheduler gestartet – stündlich")
    while True:
        await asyncio.sleep(_CHECK_INTERVAL)
        try:
            if not should_run():
                logger.debug("curator scheduler: Bedingungen nicht erfüllt – übersprungen.")
                continue
            report = await run_dry_run()
            if report:
                await bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown")
                logger.info("Curator Dry-Run-Report gesendet.")
        except Exception as e:
            logger.error(f"Curator Scheduler Fehler: {e}")
