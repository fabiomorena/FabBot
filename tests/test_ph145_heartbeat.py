"""
tests/test_ph145_heartbeat.py – Phase 145 (Issue #92)

Testet Heartbeat + Trigger-basierte Proaktivität:
- Cooldown: is_on_cooldown, is_muted, set_cooldown, mute/unmute
- evaluate_time_triggers: korrekte Trigger-Tage, kein Trigger bei anderen Tagen
- generate_proactive_message: Haiku-Aufruf + Fallback
- _run_heartbeat: sendet bei Trigger, überspringt bei Cooldown/Mute/keinem Trigger
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def cooldown_file(tmp_path):
    """Temporäre Cooldown-Datei für Tests."""
    return tmp_path / "proactive_cooldown.json"


class TestCooldown:
    def test_no_file_not_on_cooldown(self, cooldown_file):
        from agent.proactive.heartbeat import is_on_cooldown
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            assert not is_on_cooldown()

    def test_recent_send_on_cooldown(self, cooldown_file):
        from agent.proactive.heartbeat import is_on_cooldown
        cooldown_file.write_text(json.dumps({
            "last_sent_at": datetime.now(timezone.utc).isoformat()
        }))
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            assert is_on_cooldown()

    def test_old_send_not_on_cooldown(self, cooldown_file):
        from agent.proactive.heartbeat import is_on_cooldown
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        cooldown_file.write_text(json.dumps({"last_sent_at": old}))
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            assert not is_on_cooldown()

    def test_set_cooldown_writes_file(self, cooldown_file):
        from agent.proactive.heartbeat import set_cooldown
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            set_cooldown()
        data = json.loads(cooldown_file.read_text())
        assert "last_sent_at" in data

    def test_set_cooldown_then_on_cooldown(self, cooldown_file):
        from agent.proactive.heartbeat import set_cooldown, is_on_cooldown
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            set_cooldown()
            assert is_on_cooldown()


class TestMute:
    def test_not_muted_by_default(self, cooldown_file):
        from agent.proactive.heartbeat import is_muted
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            assert not is_muted()

    def test_mute_sets_future_timestamp(self, cooldown_file):
        from agent.proactive.heartbeat import mute_proactive, is_muted
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            mute_proactive(hours=24)
            assert is_muted()

    def test_expired_mute_not_muted(self, cooldown_file):
        from agent.proactive.heartbeat import is_muted
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cooldown_file.write_text(json.dumps({"muted_until": past}))
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            assert not is_muted()

    def test_unmute_clears_mute(self, cooldown_file):
        from agent.proactive.heartbeat import mute_proactive, unmute_proactive, is_muted
        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            mute_proactive(24)
            unmute_proactive()
            assert not is_muted()


class TestEvaluateTimeTriggers:
    def test_item_in_trigger_days_returns_trigger(self):
        from agent.proactive.heartbeat import evaluate_time_triggers, TRIGGER_DAYS
        for days in TRIGGER_DAYS:
            due = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
            items = [{"name": "Test", "entity_type": "task", "due_date": due}]
            result = evaluate_time_triggers(items)
            assert len(result) == 1
            assert result[0]["days_until_due"] == days

    def test_item_in_non_trigger_days_no_trigger(self):
        from agent.proactive.heartbeat import evaluate_time_triggers
        due = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        items = [{"name": "Test", "entity_type": "task", "due_date": due}]
        assert evaluate_time_triggers(items) == []

    def test_item_without_due_date_no_trigger(self):
        from agent.proactive.heartbeat import evaluate_time_triggers
        items = [{"name": "Test", "entity_type": "intent"}]
        assert evaluate_time_triggers(items) == []

    def test_overdue_item_no_trigger(self):
        from agent.proactive.heartbeat import evaluate_time_triggers
        due = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        items = [{"name": "Test", "entity_type": "task", "due_date": due}]
        assert evaluate_time_triggers(items) == []

    def test_invalid_due_date_skipped(self):
        from agent.proactive.heartbeat import evaluate_time_triggers
        items = [{"name": "Test", "entity_type": "task", "due_date": "invalid"}]
        assert evaluate_time_triggers(items) == []

    def test_multiple_items_only_triggered_returned(self):
        from agent.proactive.heartbeat import evaluate_time_triggers, TRIGGER_DAYS
        trigger_days = list(TRIGGER_DAYS)[0]
        due_trigger = (datetime.now(timezone.utc) + timedelta(days=trigger_days)).date().isoformat()
        due_no = (datetime.now(timezone.utc) + timedelta(days=15)).date().isoformat()
        items = [
            {"name": "A", "entity_type": "task", "due_date": due_trigger},
            {"name": "B", "entity_type": "event", "due_date": due_no},
        ]
        result = evaluate_time_triggers(items)
        assert len(result) == 1
        assert result[0]["name"] == "A"


class TestGenerateProactiveMessage:
    @pytest.mark.asyncio
    async def test_returns_llm_content(self):
        from agent.proactive.heartbeat import generate_proactive_message
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Hast du schon ein Hotel gebucht?")
        with patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm):
            result = await generate_proactive_message({
                "name": "Salvador", "entity_type": "place",
                "due_date": "2026-05-31", "days_until_due": 7,
                "source_context": "Reise nach Salvador Ende Mai"
            })
        assert "Hotel" in result

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        from agent.proactive.heartbeat import generate_proactive_message
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM down")
        with patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm):
            result = await generate_proactive_message({
                "name": "Steffi", "entity_type": "person",
                "days_until_due": 3, "source_context": "Steffi feiert"
            })
        assert "Steffi" in result or "3" in result


class TestRunHeartbeat:
    @pytest.mark.asyncio
    async def test_sends_message_when_triggered(self, cooldown_file):
        from bot.heartbeat_scheduler import _run_heartbeat
        trigger_days = 7
        due = (datetime.now(timezone.utc) + timedelta(days=trigger_days)).date().isoformat()
        pending = [{"name": "Reise", "entity_type": "task", "due_date": due,
                    "priority_score": 40, "source_context": "Reise planen"}]
        mock_bot = AsyncMock()

        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file), \
             patch("bot.heartbeat_scheduler.get_pending_items", return_value=pending), \
             patch("bot.heartbeat_scheduler.generate_proactive_message",
                   new_callable=AsyncMock, return_value="Hast du schon gebucht?"), \
             patch("bot.heartbeat_scheduler.set_cooldown") as mock_cooldown:
            await _run_heartbeat(mock_bot, 12345)

        assert mock_bot.send_message.called
        assert mock_cooldown.called

    @pytest.mark.asyncio
    async def test_skips_when_on_cooldown(self, cooldown_file):
        from bot.heartbeat_scheduler import _run_heartbeat
        cooldown_file.write_text(json.dumps({
            "last_sent_at": datetime.now(timezone.utc).isoformat()
        }))
        mock_bot = AsyncMock()

        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            await _run_heartbeat(mock_bot, 12345)

        assert not mock_bot.send_message.called

    @pytest.mark.asyncio
    async def test_skips_when_muted(self, cooldown_file):
        from bot.heartbeat_scheduler import _run_heartbeat
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        cooldown_file.write_text(json.dumps({"muted_until": future}))
        mock_bot = AsyncMock()

        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file):
            await _run_heartbeat(mock_bot, 12345)

        assert not mock_bot.send_message.called

    @pytest.mark.asyncio
    async def test_skips_when_no_triggers(self, cooldown_file):
        from bot.heartbeat_scheduler import _run_heartbeat
        mock_bot = AsyncMock()
        pending = [{"name": "X", "entity_type": "intent", "priority_score": 10}]

        with patch("agent.proactive.heartbeat.COOLDOWN_FILE", cooldown_file), \
             patch("bot.heartbeat_scheduler.get_pending_items", return_value=pending):
            await _run_heartbeat(mock_bot, 12345)

        assert not mock_bot.send_message.called
