"""
agent/proactive/focus_mode.py – Phase 221 (Issue #104)

Focus-Mode-Detektor: intelligentes Muting proaktiver Nachrichten bei Inaktivität.

Schwellenwerte (konfigurierbar via .env):
  FOCUS_SOFT_MUTE_MIN (default: 15) – ab hier nur noch hohe Priorität
  FOCUS_HARD_MUTE_MIN (default: 60) – ab hier gar keine proaktiven Nachrichten

Activity-Quelle: ~/.fabbot/activity.json (von bot.py bei jeder eingehenden Nachricht geschrieben).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ACTIVITY_FILE = Path.home() / ".fabbot" / "activity.json"

NORMAL = "normal"
SOFT_MUTE = "soft_mute"
HARD_MUTE = "hard_mute"


def get_last_activity_ts() -> float | None:
    """Liest letzten Aktivitäts-Timestamp aus activity.json. Gibt Unix-Timestamp zurück."""
    try:
        if _ACTIVITY_FILE.exists():
            data = json.loads(_ACTIVITY_FILE.read_text())
            ts_str = data.get("last_activity")
            if ts_str:
                return datetime.fromisoformat(ts_str).timestamp()
    except Exception as e:
        logger.debug(f"focus_mode: activity.json lesen Fehler: {e}")
    return None


def get_idle_seconds() -> float:
    """Sekunden seit letzter User-Aktivität (aus activity.json). 0.0 wenn keine Daten."""
    ts = get_last_activity_ts()
    if ts is not None:
        idle = datetime.now(timezone.utc).timestamp() - ts
        return max(0.0, idle)
    return 0.0


def get_focus_state() -> str:
    """Gibt aktuellen Focus-Zustand zurück: normal / soft_mute / hard_mute."""
    from agent.config import get_settings

    settings = get_settings()
    idle = get_idle_seconds()
    soft_threshold = settings.focus_soft_mute_min * 60
    hard_threshold = settings.focus_hard_mute_min * 60

    if idle >= hard_threshold:
        return HARD_MUTE
    if idle >= soft_threshold:
        return SOFT_MUTE
    return NORMAL


def is_focus_muted(priority: str = "normal") -> bool:
    """True wenn proaktive Nachricht unterdrückt werden soll.

    priority='high'   → nur HARD_MUTE blockiert
    priority='normal' → SOFT_MUTE und HARD_MUTE blockieren
    """
    state = get_focus_state()
    if state == HARD_MUTE:
        return True
    if state == SOFT_MUTE and priority != "high":
        return True
    return False
