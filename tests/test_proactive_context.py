"""
Tests für agent/proactive/context.py

Testet den Proaktiven Kontext-Aggregator der alle Agent-Infos
für den chat_agent bündelt: Pending Items + Heartbeat-State.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_item(
    name: str = "TestItem",
    entity_type: str = "event",
    due_date: str | None = None,
    mention_count: int = 1,
    source_context: str = "Test-Kontext",
    priority_score: int = 20,
) -> dict:
    item = {
        "name": name,
        "entity_type": entity_type,
        "mention_count": mention_count,
        "source_context": source_context,
        "priority_score": priority_score,
        "status": "open",
    }
    if due_date:
        item["due_date"] = due_date
    return item


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _in_days(n: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=n)).isoformat()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# _format_pending_items
# ---------------------------------------------------------------------------

class TestFormatPendingItems:
    """_format_pending_items() formatiert die Pending-Liste korrekt."""

    def test_empty_list_returns_empty_string(self) -> None:
        with patch("agent.proactive.pending.get_pending_items", return_value=[]):
            from agent.proactive.context import _format_pending_items
            assert _format_pending_items() == ""

    def test_single_item_contains_name(self) -> None:
        items = [_make_item(name="Konzerttickets")]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "Konzerttickets" in result

    def test_header_present(self) -> None:
        items = [_make_item()]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "Pending Items" in result or "Offene Themen" in result

    def test_entity_type_shown(self) -> None:
        items = [_make_item(entity_type="task")]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "task" in result

    def test_due_today_label(self) -> None:
        items = [_make_item(due_date=_today_str())]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "HEUTE" in result

    def test_overdue_label(self) -> None:
        items = [_make_item(due_date=_days_ago(5))]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "ÜBERFÄLLIG" in result

    def test_due_in_2_days_label(self) -> None:
        items = [_make_item(due_date=_in_days(2))]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "in 2d" in result

    def test_due_in_future_shows_date(self) -> None:
        future = _in_days(14)
        items = [_make_item(due_date=future)]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert future[:10] in result

    def test_mention_count_shown_when_gt_1(self) -> None:
        items = [_make_item(mention_count=3)]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "3x" in result

    def test_mention_count_not_shown_when_1(self) -> None:
        items = [_make_item(mention_count=1)]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "1x" not in result

    def test_source_context_included(self) -> None:
        items = [_make_item(source_context="Wichtiger Hinweis")]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "Wichtiger Hinweis" in result

    def test_multiple_items_all_present(self) -> None:
        items = [
            _make_item(name="Item A"),
            _make_item(name="Item B"),
            _make_item(name="Item C"),
        ]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "Item A" in result
        assert "Item B" in result
        assert "Item C" in result

    def test_exception_returns_empty_string(self) -> None:
        with patch("agent.proactive.pending.get_pending_items", side_effect=RuntimeError("DB down")):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert result == ""

    def test_no_due_date_no_label(self) -> None:
        items = [_make_item(due_date=None)]
        with patch("agent.proactive.pending.get_pending_items", return_value=items):
            from agent.proactive.context import _format_pending_items
            result = _format_pending_items()
        assert "fällig" not in result
        assert "ÜBERFÄLLIG" not in result


# ---------------------------------------------------------------------------
# _format_heartbeat_state
# ---------------------------------------------------------------------------

class TestFormatHeartbeatState:
    """_format_heartbeat_state() spiegelt Mute/Cooldown-Status korrekt."""

    def test_neither_muted_nor_cooldown_returns_empty(self) -> None:
        with patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import _format_heartbeat_state
            assert _format_heartbeat_state() == ""

    def test_muted_state_shown(self) -> None:
        with patch("agent.proactive.heartbeat.is_muted", return_value=True), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import _format_heartbeat_state
            result = _format_heartbeat_state()
        assert "stumm" in result.lower() or "muted" in result.lower() or "stummgeschaltet" in result.lower()

    def test_cooldown_state_shown(self) -> None:
        with patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=True):
            from agent.proactive.context import _format_heartbeat_state
            result = _format_heartbeat_state()
        assert "cooldown" in result.lower() or "Cooldown" in result

    def test_exception_returns_empty_string(self) -> None:
        with patch("agent.proactive.heartbeat.is_muted", side_effect=Exception("file missing")):
            from agent.proactive.context import _format_heartbeat_state
            assert _format_heartbeat_state() == ""


# ---------------------------------------------------------------------------
# get_proactive_context (öffentliche API)
# ---------------------------------------------------------------------------

class TestGetProactiveContext:
    """get_proactive_context() aggregiert alle Quellen korrekt."""

    def test_returns_string(self) -> None:
        with patch("agent.proactive.pending.get_pending_items", return_value=[]), \
             patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert isinstance(result, str)

    def test_empty_when_no_data(self) -> None:
        with patch("agent.proactive.pending.get_pending_items", return_value=[]), \
             patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            assert get_proactive_context() == ""

    def test_contains_pending_items(self) -> None:
        items = [_make_item(name="Brasilien-Trip", due_date=_in_days(7))]
        with patch("agent.proactive.pending.get_pending_items", return_value=items), \
             patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert "Brasilien-Trip" in result

    def test_contains_heartbeat_when_muted(self) -> None:
        with patch("agent.proactive.pending.get_pending_items", return_value=[]), \
             patch("agent.proactive.heartbeat.is_muted", return_value=True), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert result != ""

    def test_both_blocks_combined(self) -> None:
        items = [_make_item(name="Konzert")]
        with patch("agent.proactive.pending.get_pending_items", return_value=items), \
             patch("agent.proactive.heartbeat.is_muted", return_value=True), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert "Konzert" in result
        assert "stumm" in result.lower() or "stummgeschaltet" in result.lower()

    def test_fail_safe_on_all_exceptions(self) -> None:
        """Keine Exception auch wenn beide Quellen crashen."""
        with patch("agent.proactive.pending.get_pending_items", side_effect=RuntimeError("crash")), \
             patch("agent.proactive.heartbeat.is_muted", side_effect=OSError("no file")):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert isinstance(result, str)

    def test_starts_with_newlines_when_nonempty(self) -> None:
        """Nicht-leerer Kontext beginnt mit Leerzeilen für saubere Trennung im Prompt."""
        items = [_make_item(name="X")]
        with patch("agent.proactive.pending.get_pending_items", return_value=items), \
             patch("agent.proactive.heartbeat.is_muted", return_value=False), \
             patch("agent.proactive.heartbeat.is_on_cooldown", return_value=False):
            from agent.proactive.context import get_proactive_context
            result = get_proactive_context()
        assert result.startswith("\n")
