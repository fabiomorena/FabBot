"""tests/test_ph98_datetime.py – Phase 98: Datetime-Awareness

Prüft:
- get_current_datetime() liefert Berlin-Zeit (nicht UTC)
- Format korrekt: Wochentag, DD.MM.YYYY – HH:MM Uhr
- Alle Agenten-System-Prompts enthalten den Datetime-Block
"""

import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# get_current_datetime()
# ---------------------------------------------------------------------------

class TestGetCurrentDatetime:
    def test_returns_string(self):
        from agent.utils import get_current_datetime
        result = get_current_datetime()
        assert isinstance(result, str)

    def test_format(self):
        from agent.utils import get_current_datetime
        result = get_current_datetime()
        # Erwartet: "Montag, 11.04.2026 – 14:32 Uhr"
        pattern = r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag), \d{2}\.\d{2}\.\d{4} – \d{2}:\d{2} Uhr$"
        assert re.match(pattern, result), f"Format falsch: {result!r}"

    def test_not_utc(self):
        """Berlin-Zeit weicht im Sommer von UTC ab – das muss sichtbar sein."""
        from agent.utils import get_current_datetime
        # Wir mocken eine Sommerzeit-Situation: UTC 12:00 → Berlin 14:00 (CEST +2)
        fake_utc = datetime(2026, 7, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        fake_berlin = fake_utc.astimezone(ZoneInfo("Europe/Berlin"))
        with patch("agent.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fake_berlin
            result = get_current_datetime()
        assert "14:00" in result, f"Erwartet 14:00 (CEST), bekommen: {result}"

    def test_winter_time(self):
        """Winterzeit: UTC+1"""
        from agent.utils import get_current_datetime
        fake_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        fake_berlin = fake_utc.astimezone(ZoneInfo("Europe/Berlin"))
        with patch("agent.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fake_berlin
            result = get_current_datetime()
        assert "13:00" in result, f"Erwartet 13:00 (CET), bekommen: {result}"

    def test_weekday_german(self):
        from agent.utils import get_current_datetime
        # 11.04.2026 ist ein Samstag
        fake = datetime(2026, 4, 11, 14, 32, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("agent.utils.datetime") as mock_dt:
            mock_dt.now.return_value = fake
            result = get_current_datetime()
        assert result == "Samstag, 11.04.2026 – 14:32 Uhr"

    def test_all_weekdays_covered(self):
        from agent.utils import _WEEKDAYS_DE
        assert len(_WEEKDAYS_DE) == 7
        assert set(_WEEKDAYS_DE.keys()) == {0, 1, 2, 3, 4, 5, 6}


# ---------------------------------------------------------------------------
# Agent-Prompts enthalten Datetime-Marker
# Jeder Agent baut seinen Prompt dynamisch zur Laufzeit auf.
# Wir mocken get_current_datetime() und prüfen ob der String im Prompt landet.
# ---------------------------------------------------------------------------

DATETIME_MOCK = "Samstag, 11.04.2026 – 14:32 Uhr"
DATETIME_PATTERN = r"\d{2}\.\d{2}\.\d{4}"  # Datum reicht als Marker


def _get_prompt(build_fn) -> str:
    """Ruft build_fn() mit gemocktem get_current_datetime() auf."""
    with patch("agent.utils.get_current_datetime", return_value=DATETIME_MOCK):
        return build_fn()


class TestChatAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.chat_agent import _build_chat_prompt
        prompt = _get_prompt(_build_chat_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "chat_agent Prompt enthält kein Datum"


class TestWebAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.web import _build_web_prompt
        prompt = _get_prompt(_build_web_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "web_agent Prompt enthält kein Datum"


class TestMemoryAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.memory_agent import _build_memory_prompt
        prompt = _get_prompt(_build_memory_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "memory_agent Prompt enthält kein Datum"


class TestReminderAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.reminder_agent import _build_reminder_prompt
        prompt = _get_prompt(_build_reminder_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "reminder_agent Prompt enthält kein Datum"


class TestTerminalAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.terminal import _build_terminal_prompt
        prompt = _get_prompt(_build_terminal_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "terminal_agent Prompt enthält kein Datum"


class TestVisionAgentPrompt:
    def test_contains_datetime(self):
        from agent.agents.vision_agent import _build_vision_prompt
        prompt = _get_prompt(_build_vision_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "vision_agent Prompt enthält kein Datum"


class TestSupervisorPrompt:
    def test_contains_datetime(self):
        from agent.supervisor import _build_supervisor_prompt
        prompt = _get_prompt(_build_supervisor_prompt)
        assert DATETIME_MOCK in prompt or re.search(DATETIME_PATTERN, prompt), \
            "supervisor Prompt enthält kein Datum"
