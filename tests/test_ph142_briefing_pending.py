"""
tests/test_ph142_briefing_pending.py – Phase 142 (Issue #89)

Testet die Pending-Items-Integration im Morning Briefing:
- _format_pending_items() Formatierung und Icons
- generate_briefing() enthält Pending-Sektion wenn Items vorhanden
- generate_briefing() lässt Sektion weg wenn keine Items
- Fehlertoleranz (get_pending_items wirft → kein Crash)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


class TestFormatPendingItems:
    def test_empty_list_returns_empty_string(self):
        from bot.briefing import _format_pending_items
        assert _format_pending_items([]) == ""

    def test_task_gets_checkmark_icon(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "task", "name": "Reiseplanung", "priority_score": 30}]
        result = _format_pending_items(items)
        assert "✅" in result
        assert "Reiseplanung" in result

    def test_event_gets_calendar_icon(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "event", "name": "Steffi Geburtstag", "priority_score": 40}]
        result = _format_pending_items(items)
        assert "📅" in result

    def test_intent_gets_thought_icon(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "intent", "name": "Gitarre üben", "priority_score": 10}]
        result = _format_pending_items(items)
        assert "💭" in result

    def test_person_gets_person_icon(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "person", "name": "Marco", "priority_score": 10}]
        result = _format_pending_items(items)
        assert "👤" in result

    def test_place_gets_pin_icon(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "place", "name": "Salvador", "priority_score": 5}]
        result = _format_pending_items(items)
        assert "📍" in result

    def test_unknown_type_gets_bullet(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "other", "name": "X", "priority_score": 5}]
        result = _format_pending_items(items)
        assert "•" in result

    def test_due_date_shown_formatted(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "task", "name": "Flug buchen", "due_date": "2026-05-31", "priority_score": 40}]
        result = _format_pending_items(items)
        assert "31.05." in result

    def test_no_due_date_no_parenthesis(self):
        from bot.briefing import _format_pending_items
        items = [{"entity_type": "task", "name": "Flug buchen", "priority_score": 20}]
        result = _format_pending_items(items)
        assert "(" not in result

    def test_multiple_items_each_on_own_line(self):
        from bot.briefing import _format_pending_items
        items = [
            {"entity_type": "task", "name": "A", "priority_score": 30},
            {"entity_type": "event", "name": "B", "priority_score": 20},
        ]
        result = _format_pending_items(items)
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 2


class TestGenerateBriefingWithPending:
    @pytest.mark.asyncio
    async def test_briefing_includes_pending_section_when_items_exist(self):
        from bot.briefing import generate_briefing
        pending = [{"entity_type": "task", "name": "Reise planen", "priority_score": 40}]

        with patch("bot.briefing._get_weather_berlin", new_callable=AsyncMock, return_value="Sonnig"), \
             patch("bot.briefing._fetch_web", new_callable=AsyncMock, return_value="Keine News"), \
             patch("bot.briefing._get_calendar_today", return_value="Keine Termine"), \
             patch("bot.briefing.get_pending_items", return_value=pending):
            result = await generate_briefing()

        assert "Offene Punkte" in result
        assert "Reise planen" in result

    @pytest.mark.asyncio
    async def test_briefing_omits_pending_section_when_empty(self):
        from bot.briefing import generate_briefing

        with patch("bot.briefing._get_weather_berlin", new_callable=AsyncMock, return_value="Sonnig"), \
             patch("bot.briefing._fetch_web", new_callable=AsyncMock, return_value="Keine News"), \
             patch("bot.briefing._get_calendar_today", return_value="Keine Termine"), \
             patch("bot.briefing.get_pending_items", return_value=[]):
            result = await generate_briefing()

        assert "Offene Punkte" not in result

    @pytest.mark.asyncio
    async def test_briefing_fail_safe_on_pending_error(self):
        from bot.briefing import generate_briefing

        with patch("bot.briefing._get_weather_berlin", new_callable=AsyncMock, return_value="Sonnig"), \
             patch("bot.briefing._fetch_web", new_callable=AsyncMock, return_value="Keine News"), \
             patch("bot.briefing._get_calendar_today", return_value="Keine Termine"), \
             patch("bot.briefing.get_pending_items", side_effect=Exception("DB error")):
            result = await generate_briefing()

        assert "Guten Morgen" in result  # Briefing trotzdem geliefert
