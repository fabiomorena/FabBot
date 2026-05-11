"""
agent/_bot_bridge.py – Bricht die zirkuläre Import-Kette bot.bot ↔ agent.agents.

bot.bot registriert beim Start seine send_message-Funktion hier.
Agents rufen send_status() auf ohne bot.bot direkt zu importieren.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_send_fn: Callable[[int, str], Awaitable[None]] | None = None


def register(fn: Callable[[int, str], Awaitable[None]]) -> None:
    global _send_fn
    _send_fn = fn


async def send_status(chat_id: int, text: str) -> None:
    if _send_fn is None:
        return
    try:
        await _send_fn(chat_id, text)
    except Exception as e:
        logger.debug(f"send_status fehlgeschlagen (ignoriert): {e}")
