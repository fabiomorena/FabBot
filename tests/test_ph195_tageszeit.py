"""
tests/test_ph195_tageszeit.py – Phase 195 (Issues #103 + #109)

Testet:
- is_quiet_hours / get_berlin_hour: korrekte Sperrung nach Tageszeit
- _run_heartbeat: kein proaktiver Send während Ruhestunden
- Evening Check-in: Idempotenz, Send-Logik, LLM-Fehler-Fallback
"""

import json
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


# ── Quiet Hours ───────────────────────────────────────────────────────────────


class TestQuietHours:
    def _patch_hour(self, hour: int):

        dt = MagicMock()
        dt.hour = hour
        return patch(
            "agent.proactive.heartbeat.datetime",
            **{"now.return_value": dt},
        )

    def test_night_hour_is_quiet(self):
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=2):
            assert is_quiet_hours() is True

    def test_day_hour_is_not_quiet(self):
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=10):
            assert is_quiet_hours() is False

    def test_afternoon_is_not_quiet(self):
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=15):
            assert is_quiet_hours() is False

    def test_boundary_quiet_start(self):
        """22:00 ist Beginn der Ruhestunden → quiet."""
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=22):
            assert is_quiet_hours() is True

    def test_boundary_quiet_end(self):
        """08:00 ist Ende der Ruhestunden → nicht quiet."""
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=8):
            assert is_quiet_hours() is False

    def test_hour_before_end_is_quiet(self):
        """07:59 → noch quiet."""
        from agent.proactive.heartbeat import is_quiet_hours

        with patch("agent.proactive.heartbeat.get_berlin_hour", return_value=7):
            assert is_quiet_hours() is True


# ── Heartbeat Guard ───────────────────────────────────────────────────────────


class TestHeartbeatQuietHoursGuard:
    @pytest.mark.asyncio
    async def test_no_proactive_during_quiet_hours(self):
        """_run_heartbeat darf keine Nachricht senden wenn quiet hours aktiv."""
        mock_bot = AsyncMock()

        with (
            patch("bot.heartbeat_scheduler.run_api_health_check", new=AsyncMock()),
            patch("bot.heartbeat_scheduler.is_on_cooldown", return_value=False),
            patch("bot.heartbeat_scheduler.is_muted", return_value=False),
            patch("bot.heartbeat_scheduler.is_quiet_hours", return_value=True),
            patch("bot.heartbeat_scheduler.get_pending_items", return_value=[]),
        ):
            from bot.heartbeat_scheduler import _run_heartbeat

            await _run_heartbeat(mock_bot, chat_id=123)
            mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_proactive_allowed_outside_quiet_hours(self):
        """_run_heartbeat sendet wenn kein Cooldown, kein Mute, nicht quiet."""
        mock_bot = AsyncMock()
        trigger = {"name": "Test", "days_until_due": 1, "entity_type": "task"}

        with (
            patch("bot.heartbeat_scheduler.run_api_health_check", new=AsyncMock()),
            patch("bot.heartbeat_scheduler.is_on_cooldown", return_value=False),
            patch("bot.heartbeat_scheduler.is_muted", return_value=False),
            patch("bot.heartbeat_scheduler.is_quiet_hours", return_value=False),
            patch("bot.heartbeat_scheduler.get_pending_items", return_value=[trigger]),
            patch("bot.heartbeat_scheduler.evaluate_time_triggers", return_value=[trigger]),
            patch("bot.heartbeat_scheduler._send_proactive", new=AsyncMock()) as mock_send,
        ):
            from bot.heartbeat_scheduler import _run_heartbeat

            await _run_heartbeat(mock_bot, chat_id=123)
            mock_send.assert_called_once()


# ── Evening Check-in ──────────────────────────────────────────────────────────


@pytest.fixture
def checkin_state_file(tmp_path):
    return tmp_path / "evening_checkin_state.json"


class TestEveningCheckinAlreadySent:
    def test_no_file_not_sent(self, checkin_state_file):
        from bot.evening_checkin import _already_sent_today

        with patch("bot.evening_checkin._CHECKIN_STATE_FILE", checkin_state_file):
            assert _already_sent_today() is False

    def test_sent_today_returns_true(self, checkin_state_file):
        from bot.evening_checkin import _already_sent_today

        checkin_state_file.write_text(json.dumps({"last_sent_date": date.today().isoformat()}))
        with patch("bot.evening_checkin._CHECKIN_STATE_FILE", checkin_state_file):
            assert _already_sent_today() is True

    def test_sent_yesterday_returns_false(self, checkin_state_file):
        from datetime import timedelta
        from bot.evening_checkin import _already_sent_today

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        checkin_state_file.write_text(json.dumps({"last_sent_date": yesterday}))
        with patch("bot.evening_checkin._CHECKIN_STATE_FILE", checkin_state_file):
            assert _already_sent_today() is False

    def test_corrupted_file_not_sent(self, checkin_state_file):
        from bot.evening_checkin import _already_sent_today

        checkin_state_file.write_text("not-valid-json")
        with patch("bot.evening_checkin._CHECKIN_STATE_FILE", checkin_state_file):
            assert _already_sent_today() is False


class TestEveningCheckinGeneration:
    @pytest.mark.asyncio
    async def test_generates_question_from_context(self):
        """Mit echtem Kontext wird der LLM aufgerufen und das Ergebnis zurückgegeben."""
        from bot.evening_checkin import _generate_checkin_question

        mock_response = MagicMock()
        mock_response.content = "Wie lief dein Tag?"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        fake_msg = MagicMock()
        fake_msg.type = "human"
        fake_msg.content = "Ich hab heute an Phase 203 gearbeitet."

        with (
            patch("bot.session_summary._get_messages_from_state", new=AsyncMock(return_value=[fake_msg])),
            patch("bot.session_summary._filter_messages", return_value=[fake_msg]),
            patch("bot.evening_checkin._filter_checkin_context", return_value=[fake_msg]),
            patch(
                "bot.session_summary._format_for_summary", return_value="User: Ich hab heute an Phase 203 gearbeitet."
            ),
            patch("agent.llm.get_fast_llm", return_value=mock_llm),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == "Wie lief dein Tag?"

    @pytest.mark.asyncio
    async def test_empty_context_returns_fallback_without_llm(self):
        """Ohne Kontext wird sofort der Fallback zurückgegeben, kein LLM-Call."""
        from bot.evening_checkin import _generate_checkin_question, _FALLBACK_QUESTION

        mock_llm = AsyncMock()

        with (
            patch("bot.session_summary._get_messages_from_state", new=AsyncMock(return_value=[])),
            patch("bot.session_summary._filter_messages", return_value=[]),
            patch("bot.evening_checkin._filter_checkin_context", return_value=[]),
            patch("bot.session_summary._format_for_summary", return_value=""),
            patch("agent.llm.get_fast_llm", return_value=mock_llm),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == _FALLBACK_QUESTION
            mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error_returns_fallback(self):
        from bot.evening_checkin import _generate_checkin_question, _FALLBACK_QUESTION

        with patch(
            "bot.session_summary._get_messages_from_state",
            side_effect=Exception("LLM nicht erreichbar"),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == _FALLBACK_QUESTION

    @pytest.mark.asyncio
    async def test_empty_llm_response_returns_fallback(self):
        """Leere LLM-Antwort bei vorhandenem Kontext → Fallback."""
        from bot.evening_checkin import _generate_checkin_question, _FALLBACK_QUESTION

        mock_response = MagicMock()
        mock_response.content = "   "
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        fake_msg = MagicMock()
        fake_msg.type = "human"
        fake_msg.content = "Heute war ein langer Tag."

        with (
            patch("bot.session_summary._get_messages_from_state", new=AsyncMock(return_value=[fake_msg])),
            patch("bot.session_summary._filter_messages", return_value=[fake_msg]),
            patch("bot.evening_checkin._filter_checkin_context", return_value=[fake_msg]),
            patch("bot.session_summary._format_for_summary", return_value="User: Heute war ein langer Tag."),
            patch("agent.llm.get_fast_llm", return_value=mock_llm),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == _FALLBACK_QUESTION
