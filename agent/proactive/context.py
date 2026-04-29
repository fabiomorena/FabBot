"""
agent/proactive/context.py

Proaktiver Kontext-Aggregator: fasst alle Informationen anderer Agents
zusammen und stellt sie dem chat_agent als lesbaren Block bereit.

Quellen:
- Pending Items (ChromaDB entities, sortiert nach Priorität)
- Heartbeat-State (Cooldown, Muted)

API: get_proactive_context() → str  (fail-safe, nie raise)
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _format_pending_items() -> str:
    try:
        from agent.proactive.pending import get_pending_items

        items = get_pending_items(limit=8)
        if not items:
            return ""

        today = datetime.now(timezone.utc).date()
        lines = ["## Offene Themen & Aufgaben (Pending Items)"]
        for item in items:
            name = item.get("name", "?")
            entity_type = item.get("entity_type", "")
            due = item.get("due_date", "")
            context = item.get("source_context", "")
            mention_count = item.get("mention_count", 1)

            due_str = ""
            if due:
                try:
                    due_date = datetime.strptime(due[:10], "%Y-%m-%d").date()
                    days = (due_date - today).days
                    if days < 0:
                        due_str = f" [ÜBERFÄLLIG seit {abs(days)}d]"
                    elif days == 0:
                        due_str = " [HEUTE fällig]"
                    elif days <= 3:
                        due_str = f" [in {days}d fällig]"
                    else:
                        due_str = f" [fällig: {due[:10]}]"
                except (ValueError, TypeError):
                    due_str = f" [fällig: {due[:10]}]"

            mentions_str = f", {mention_count}x erwähnt" if mention_count > 1 else ""
            type_str = f"[{entity_type}] " if entity_type else ""
            lines.append(f"- {type_str}{name}{due_str}{mentions_str}: {context}")

        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Pending Items für context_aggregator fehlgeschlagen: {e}")
        return ""


def _format_heartbeat_state() -> str:
    try:
        from agent.proactive.heartbeat import is_on_cooldown, is_muted

        parts = []
        if is_muted():
            parts.append("Proaktive Nachrichten: stummgeschaltet")
        elif is_on_cooldown():
            parts.append("Proaktive Nachrichten: Cooldown aktiv")
        return "\n".join(parts)
    except Exception as e:
        logger.debug(f"Heartbeat-State für context_aggregator fehlgeschlagen: {e}")
        return ""


def get_proactive_context() -> str:
    """Aggregiert alle proaktiven Kontext-Informationen für den chat_agent.

    Fail-safe: gibt immer einen String zurück, nie eine Exception.
    """
    blocks = []

    pending = _format_pending_items()
    if pending:
        blocks.append(pending)

    heartbeat = _format_heartbeat_state()
    if heartbeat:
        blocks.append(heartbeat)

    if not blocks:
        return ""

    return "\n\n" + "\n\n".join(blocks)
