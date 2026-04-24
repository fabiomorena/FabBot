"""
agent/proactive/heartbeat.py – Phase 145 (Issue #92)

Heartbeat-Logik: Cooldown-Management, Trigger-Evaluation, Nachrichtengenerierung.

API:
  is_on_cooldown() → bool
  is_muted() → bool
  set_cooldown() → None
  mute_proactive(hours) → None
  unmute_proactive() → None
  evaluate_time_triggers(pending_items) → list[dict]
  generate_proactive_message(trigger_item) → str
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

COOLDOWN_FILE = Path.home() / ".fabbot" / "proactive_cooldown.json"
COOLDOWN_HOURS = 6
TRIGGER_DAYS: set[int] = {7, 3, 1}


def _load_cooldown() -> dict:
    try:
        return json.loads(COOLDOWN_FILE.read_text())
    except Exception:
        return {}


def _save_cooldown(data: dict) -> None:
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_FILE.write_text(json.dumps(data))


def is_on_cooldown() -> bool:
    last_sent = _load_cooldown().get("last_sent_at")
    if not last_sent:
        return False
    try:
        dt = datetime.fromisoformat(last_sent)
        return (datetime.now(timezone.utc) - dt).total_seconds() < COOLDOWN_HOURS * 3600
    except ValueError:
        return False


def is_muted() -> bool:
    muted_until = _load_cooldown().get("muted_until")
    if not muted_until:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(muted_until)
    except ValueError:
        return False


def set_cooldown() -> None:
    data = _load_cooldown()
    data["last_sent_at"] = datetime.now(timezone.utc).isoformat()
    _save_cooldown(data)


def mute_proactive(hours: int = 24) -> None:
    data = _load_cooldown()
    data["muted_until"] = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    _save_cooldown(data)


def unmute_proactive() -> None:
    data = _load_cooldown()
    data.pop("muted_until", None)
    _save_cooldown(data)


def evaluate_time_triggers(pending_items: list[dict]) -> list[dict]:
    """Gibt Items zurück deren Fälligkeit genau in TRIGGER_DAYS Tagen liegt."""
    today = datetime.now(timezone.utc).date()
    triggered = []
    for item in pending_items:
        due_str = item.get("due_date")
        if not due_str:
            continue
        try:
            due = datetime.strptime(due_str[:10], "%Y-%m-%d").date()
            days = (due - today).days
            if days in TRIGGER_DAYS:
                triggered.append({**item, "days_until_due": days})
        except (ValueError, TypeError):
            continue
    return triggered


def _get_llm():
    from agent.llm import get_fast_llm
    return get_fast_llm()


async def generate_proactive_message(trigger_item: dict) -> str:
    """Haiku generiert eine kurze proaktive Nachricht für das getriggerte Item."""
    try:
        from langchain_core.messages import HumanMessage
        llm = _get_llm()
        days = trigger_item.get("days_until_due", "?")
        name = trigger_item.get("name", "")
        due = trigger_item.get("due_date", "")
        entity_type = trigger_item.get("entity_type", "")
        context = trigger_item.get("source_context", "")

        prompt = f"""Schreibe eine kurze, freundliche proaktive Telegram-Nachricht für Fabio.

Kontext:
- {entity_type} "{name}" ist in {days} Tag(en) fällig ({due})
- Ursprünglicher Kontext: {context}

Regeln:
- Max. 2 Sätze
- Direkt und persönlich ("Du wolltest...", "Hast du schon...")
- Kein "Guten Morgen", keine förmliche Begrüßung
- Deutsch, keine URLs"""

        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=8.0,
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return content.strip() or _fallback_message(trigger_item)
    except Exception as e:
        logger.warning(f"generate_proactive_message Fehler: {e}")
        return _fallback_message(trigger_item)


def _fallback_message(item: dict) -> str:
    name = item.get("name", "")
    days = item.get("days_until_due", "?")
    return f"Erinnerung: '{name}' ist in {days} Tag(en) fällig."
