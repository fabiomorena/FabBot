"""
tests/test_ph213_heartbeat_date_location.py – Phase 213 (Issue #248)

Testet dass der Heartbeat-Agent das heutige Datum und den aktuellen
Aufenthaltsort kennt und korrekt in den LLM-Prompt einbettet.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.proactive.heartbeat import (
    _build_relationship_alert_prompt,
    _build_time_trigger_prompt,
    _fetch_location_ctx,
    _gather_heartbeat_context,
    _get_today_str,
)

TRIGGER_TIME = {
    "name": "Familienbesuch",
    "entity_type": "event",
    "due_date": "2026-05-21",
    "days_until_due": 0,
    "source_context": "Besuch in Kassel",
}

TRIGGER_RELATIONSHIP = {
    "name": "Oma",
    "entity_type": "person",
    "days_since_mention": 14,
    "source_context": "Letzter Kontakt vor zwei Wochen",
    "trigger_type": "relationship_alert",
}


# ── _get_today_str ────────────────────────────────────────────────────────────


class TestGetTodayStr:
    def test_returns_german_date_format(self):
        result = _get_today_str()
        parts = result.split(".")
        assert len(parts) == 3
        day, month, year = parts
        assert day.isdigit() and 1 <= int(day) <= 31
        assert month.isdigit() and 1 <= int(month) <= 12
        assert len(year) == 4 and year.isdigit()

    def test_uses_berlin_timezone(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        berlin_date = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y")
        assert _get_today_str() == berlin_date


# ── _fetch_location_ctx ───────────────────────────────────────────────────────


class TestFetchLocationCtx:
    async def test_returns_location_from_memory(self):
        mock_results = [
            {"label": "Standort", "document": "Fabio ist bis 22.05. in Kassel beim Familienbesuch."},
        ]
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=mock_results):
            result = await _fetch_location_ctx()
        assert "Kassel" in result

    async def test_returns_empty_on_no_results(self):
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=[]):
            result = await _fetch_location_ctx()
        assert result == ""

    async def test_returns_empty_on_exception(self):
        with patch("agent.retrieval.search", new_callable=AsyncMock, side_effect=Exception("chroma down")):
            result = await _fetch_location_ctx()
        assert result == ""

    async def test_truncates_long_document_to_200_chars(self):
        long_doc = "x" * 500
        mock_results = [{"label": "Test", "document": long_doc}]
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=mock_results):
            result = await _fetch_location_ctx()
        assert len(result) <= 200

    async def test_combines_multiple_results(self):
        mock_results = [
            {"label": "A", "document": "Fabio ist in Kassel."},
            {"label": "B", "document": "Ankunft am 20.05."},
        ]
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=mock_results):
            result = await _fetch_location_ctx()
        assert "Kassel" in result
        assert "Ankunft" in result


# ── _build_time_trigger_prompt – date + location ──────────────────────────────


class TestBuildTimeTriggerPromptDateLocation:
    def _make_ctx(self, location: str = "") -> dict:
        return {"profile": "User: Fabio", "memory": "", "sessions": "", "location": location}

    def test_contains_today_date(self):
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, self._make_ctx())
        assert _get_today_str() in prompt

    def test_contains_heute_label(self):
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, self._make_ctx())
        assert "Heute:" in prompt

    def test_contains_location_when_present(self):
        ctx = self._make_ctx(location="Fabio ist in Kassel bis 22.05.")
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, ctx)
        assert "Kassel" in prompt

    def test_contains_aktueller_aufenthalt_section(self):
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, self._make_ctx())
        assert "Aktueller Aufenthalt" in prompt

    def test_fallback_text_when_no_location(self):
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, self._make_ctx())
        assert "kein Standort bekannt" in prompt

    def test_contains_rule_for_already_present(self):
        prompt = _build_time_trigger_prompt(TRIGGER_TIME, self._make_ctx())
        assert "steht bevor" in prompt


# ── _build_relationship_alert_prompt – date + location ───────────────────────


class TestBuildRelationshipAlertPromptDateLocation:
    def _make_ctx(self, location: str = "") -> dict:
        return {"profile": "User: Fabio", "memory": "", "sessions": "", "location": location}

    def test_contains_today_date(self):
        prompt = _build_relationship_alert_prompt(TRIGGER_RELATIONSHIP, self._make_ctx())
        assert _get_today_str() in prompt

    def test_contains_heute_label(self):
        prompt = _build_relationship_alert_prompt(TRIGGER_RELATIONSHIP, self._make_ctx())
        assert "Heute:" in prompt

    def test_contains_aktueller_aufenthalt_section(self):
        prompt = _build_relationship_alert_prompt(TRIGGER_RELATIONSHIP, self._make_ctx())
        assert "Aktueller Aufenthalt" in prompt

    def test_contains_location_when_present(self):
        ctx = self._make_ctx(location="Fabio ist in Kassel.")
        prompt = _build_relationship_alert_prompt(TRIGGER_RELATIONSHIP, ctx)
        assert "Kassel" in prompt


# ── _gather_heartbeat_context – location slot ─────────────────────────────────


class TestGatherHeartbeatContextLocation:
    async def test_returns_location_key(self):
        with (
            patch("agent.proactive.heartbeat._fetch_profile_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_session_ctx", new_callable=AsyncMock, return_value=""),
            patch(
                "agent.proactive.heartbeat._fetch_location_ctx",
                new_callable=AsyncMock,
                return_value="Fabio ist in Kassel",
            ),
        ):
            ctx = await _gather_heartbeat_context(TRIGGER_TIME)
        assert "location" in ctx
        assert ctx["location"] == "Fabio ist in Kassel"

    async def test_timeout_includes_location_empty(self):
        async def slow():
            await asyncio.sleep(10)
            return "data"

        with (
            patch("agent.proactive.heartbeat._fetch_profile_ctx", side_effect=slow),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", side_effect=lambda q: slow()),
            patch("agent.proactive.heartbeat._fetch_session_ctx", side_effect=lambda n: slow()),
            patch("agent.proactive.heartbeat._fetch_location_ctx", side_effect=slow),
            patch("agent.proactive.heartbeat.CONTEXT_FETCH_TIMEOUT", 0.1),
        ):
            ctx = await _gather_heartbeat_context(TRIGGER_TIME)
        assert ctx == {"profile": "", "memory": "", "sessions": "", "location": ""}


# ── generate_proactive_message – Prompt enthält Datum ────────────────────────


class TestGenerateProactiveMessageDate:
    async def test_prompt_contains_today(self):
        from agent.proactive.heartbeat import generate_proactive_message

        captured = {}

        async def fake_ainvoke(messages, **kwargs):
            captured["prompt"] = messages[0].content
            return MagicMock(content="Du bist gerade in Kassel – genieß die Zeit!")

        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke

        with (
            patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm),
            patch("agent.proactive.heartbeat._fetch_profile_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_session_ctx", new_callable=AsyncMock, return_value=""),
            patch(
                "agent.proactive.heartbeat._fetch_location_ctx",
                new_callable=AsyncMock,
                return_value="Fabio ist in Kassel bis 22.05.",
            ),
        ):
            result = await generate_proactive_message(TRIGGER_TIME)

        assert _get_today_str() in captured["prompt"]
        assert "Kassel" in captured["prompt"]
        assert "Kassel" in result
