# agent/utils.py
"""
FabBot – gemeinsame Hilfsfunktionen für alle Agenten.

get_current_datetime()  →  lokale Berliner Zeit als lesbarer String
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_TZ_BERLIN = ZoneInfo("Europe/Berlin")

_WEEKDAYS_DE = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}


def extract_llm_text(content) -> str:
    """Normalisiert LLM-Response-Content zu einem einzelnen String.
    Claude gibt manchmal list[dict] statt str zurück (bei extended thinking o.ä.).
    """
    if isinstance(content, list):
        return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content if isinstance(content, str) else str(content)


def get_current_datetime() -> str:
    """Gibt das aktuelle Datum und die Uhrzeit in der Zeitzone Europe/Berlin zurück.

    Format: "Montag, 11.04.2026 – 14:32 Uhr"

    Wird von allen Agenten beim Aufbau des System-Prompts aufgerufen,
    damit Datum und Uhrzeit immer aktuell und konsistent sind.
    """
    now = datetime.now(_TZ_BERLIN)
    weekday = _WEEKDAYS_DE[now.weekday()]
    return f"{weekday}, {now.strftime('%d.%m.%Y')} – {now.strftime('%H:%M')} Uhr"
