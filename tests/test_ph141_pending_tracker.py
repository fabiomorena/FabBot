"""
tests/test_ph141_pending_tracker.py – Phase 141 (Issue #88)

Testet den Pending Items Tracker:
- Prioritätsscore-Berechnung (due_date, mention_count, entity_type)
- Sortierung nach Score
- ChromaDB-Integration (gemockt)
- Fehlertoleranz
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


class TestIsStale:
    def test_no_due_date_not_stale(self):
        from agent.proactive.pending import _is_stale
        assert not _is_stale(None)

    def test_future_date_not_stale(self):
        from agent.proactive.pending import _is_stale
        future = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
        assert not _is_stale(future)

    def test_today_not_stale(self):
        from agent.proactive.pending import _is_stale
        today = datetime.now(timezone.utc).date().isoformat()
        assert not _is_stale(today)

    def test_yesterday_not_stale(self):
        from agent.proactive.pending import _is_stale
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        assert not _is_stale(yesterday)

    def test_two_days_ago_is_stale(self):
        from agent.proactive.pending import _is_stale
        old = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        assert _is_stale(old)

    def test_two_weeks_ago_is_stale(self):
        from agent.proactive.pending import _is_stale
        old = (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
        assert _is_stale(old)

    def test_stale_items_excluded_from_get_pending(self):
        from agent.proactive.pending import get_pending_items
        old = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1", "id2"],
            "metadatas": [
                {"entity_type": "event", "name": "Altes Event", "due_date": old,
                 "mention_count": 1, "status": "open"},
                {"entity_type": "task", "name": "Zukünftige Aufgabe", "due_date": future,
                 "mention_count": 1, "status": "open"},
            ],
            "documents": ["ctx1", "ctx2"],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = get_pending_items()
        assert len(result) == 1
        assert result[0]["name"] == "Zukünftige Aufgabe"


class TestDueDateScore:
    def test_no_date_returns_zero(self):
        from agent.proactive.pending import _due_date_score
        assert _due_date_score(None) == 0

    def test_overdue_returns_50(self):
        from agent.proactive.pending import _due_date_score
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        assert _due_date_score(yesterday) == 50

    def test_today_returns_48(self):
        from agent.proactive.pending import _due_date_score
        today = datetime.now(timezone.utc).date().isoformat()
        assert _due_date_score(today) == 48

    def test_3_days_returns_40(self):
        from agent.proactive.pending import _due_date_score
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
        assert _due_date_score(soon) == 40

    def test_7_days_returns_30(self):
        from agent.proactive.pending import _due_date_score
        week = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
        assert _due_date_score(week) == 30

    def test_30_days_returns_20(self):
        from agent.proactive.pending import _due_date_score
        month = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        assert _due_date_score(month) == 20

    def test_90_days_returns_10(self):
        from agent.proactive.pending import _due_date_score
        quarter = (datetime.now(timezone.utc) + timedelta(days=90)).date().isoformat()
        assert _due_date_score(quarter) == 10

    def test_far_future_returns_5(self):
        from agent.proactive.pending import _due_date_score
        future = (datetime.now(timezone.utc) + timedelta(days=200)).date().isoformat()
        assert _due_date_score(future) == 5

    def test_invalid_date_returns_zero(self):
        from agent.proactive.pending import _due_date_score
        assert _due_date_score("not-a-date") == 0


class TestMentionScore:
    def test_one_mention(self):
        from agent.proactive.pending import _mention_score
        assert _mention_score(1) == 5

    def test_six_mentions_capped_at_30(self):
        from agent.proactive.pending import _mention_score
        assert _mention_score(6) == 30

    def test_ten_mentions_still_capped(self):
        from agent.proactive.pending import _mention_score
        assert _mention_score(10) == 30

    def test_zero_mentions(self):
        from agent.proactive.pending import _mention_score
        assert _mention_score(0) == 0


class TestPriorityScore:
    def test_task_with_due_date_scores_high(self):
        from agent.proactive.pending import _priority_score
        today = datetime.now(timezone.utc).date().isoformat()
        meta = {"entity_type": "task", "due_date": today, "mention_count": 3}
        score = _priority_score(meta)
        assert score == 48 + 15 + 20  # due=48, mention=15, type=20

    def test_place_no_date_scores_low(self):
        from agent.proactive.pending import _priority_score
        meta = {"entity_type": "place", "mention_count": 1}
        score = _priority_score(meta)
        assert score == 0 + 5 + 5  # due=0, mention=5, type=5

    def test_unknown_type_zero_type_score(self):
        from agent.proactive.pending import _priority_score
        meta = {"entity_type": "unknown", "mention_count": 1}
        assert _priority_score(meta) == 5  # nur mention_score


class TestGetPendingItems:
    def test_returns_empty_if_no_collection(self):
        from agent.proactive.pending import get_pending_items
        with patch("agent.proactive.pending._get_entities_collection", return_value=None):
            result = get_pending_items()
        assert result == []

    def test_returns_sorted_by_priority(self):
        from agent.proactive.pending import get_pending_items
        today = datetime.now(timezone.utc).date().isoformat()
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "metadatas": [
                {"entity_type": "place", "mention_count": 1, "status": "open"},
                {"entity_type": "task", "due_date": today, "mention_count": 3, "status": "open"},
            ],
            "documents": ["ctx1", "ctx2"],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            result = get_pending_items()
        assert result[0]["entity_type"] == "task"
        assert result[1]["entity_type"] == "place"
        assert result[0]["priority_score"] > result[1]["priority_score"]

    def test_limit_respected(self):
        from agent.proactive.pending import get_pending_items
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2", "id3"],
            "metadatas": [
                {"entity_type": "task", "mention_count": 1, "status": "open"},
                {"entity_type": "event", "mention_count": 1, "status": "open"},
                {"entity_type": "person", "mention_count": 1, "status": "open"},
            ],
            "documents": ["ctx1", "ctx2", "ctx3"],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            result = get_pending_items(limit=2)
        assert len(result) == 2

    def test_chromadb_error_returns_empty(self):
        from agent.proactive.pending import get_pending_items
        mock_collection = MagicMock()
        mock_collection.get.side_effect = Exception("DB error")
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            result = get_pending_items()
        assert result == []

    def test_item_has_priority_score_field(self):
        from agent.proactive.pending import get_pending_items
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"entity_type": "person", "mention_count": 2, "status": "open"}],
            "documents": ["some context"],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            result = get_pending_items()
        assert "priority_score" in result[0]
        assert isinstance(result[0]["priority_score"], int)

    def test_collection_queried_with_open_status_filter(self):
        from agent.proactive.pending import get_pending_items
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": [], "documents": []}
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            get_pending_items()
        call_kwargs = mock_collection.get.call_args[1]
        assert call_kwargs.get("where") == {"status": "open"}

    def test_item_includes_id_and_document(self):
        from agent.proactive.pending import get_pending_items
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["abc123"],
            "metadatas": [{"entity_type": "event", "mention_count": 1, "status": "open"}],
            "documents": ["Steffi feiert 70."],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_collection):
            result = get_pending_items()
        assert result[0]["id"] == "abc123"
        assert result[0]["document"] == "Steffi feiert 70."
