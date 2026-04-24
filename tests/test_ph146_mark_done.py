"""
tests/test_ph146_mark_done.py – Phase 146

Testet mark_done():
- Partial/case-insensitive Match → Status auf "done" gesetzt
- Kein Match → leere Liste, kein Crash
- Mehrere Matches → alle markiert
- ChromaDB-Fehler → kein Crash
"""

import pytest
from unittest.mock import MagicMock, patch


class TestMarkDone:
    def test_exact_match_marks_done(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "Hotel buchen", "status": "open", "entity_type": "task", "mention_count": 1}],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = mark_done("Hotel buchen")
        assert result == ["Hotel buchen"]
        assert mock_col.update.called

    def test_partial_match_case_insensitive(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "Hotel buchen", "status": "open", "entity_type": "task", "mention_count": 1}],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = mark_done("hotel")
        assert result == ["Hotel buchen"]

    def test_no_match_returns_empty(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "Hotel buchen", "status": "open", "entity_type": "task", "mention_count": 1}],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = mark_done("xyz")
        assert result == []
        assert not mock_col.update.called

    def test_multiple_matches_all_marked(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1", "id2"],
            "metadatas": [
                {"name": "Flug buchen", "status": "open", "entity_type": "task", "mention_count": 1},
                {"name": "Hotel buchen", "status": "open", "entity_type": "task", "mention_count": 1},
            ],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = mark_done("buchen")
        assert len(result) == 2
        assert mock_col.update.call_count == 2

    def test_update_sets_status_done(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "Test", "status": "open", "entity_type": "task", "mention_count": 2}],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            mark_done("Test")
        update_kwargs = mock_col.update.call_args[1]
        assert update_kwargs["metadatas"][0]["status"] == "done"

    def test_no_collection_returns_empty(self):
        from agent.proactive.pending import mark_done
        with patch("agent.proactive.pending._get_entities_collection", return_value=None):
            result = mark_done("test")
        assert result == []

    def test_chromadb_error_returns_empty(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.side_effect = Exception("DB error")
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            result = mark_done("test")
        assert result == []

    def test_preserves_existing_metadata(self):
        from agent.proactive.pending import mark_done
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "Test", "status": "open", "entity_type": "event",
                           "mention_count": 3, "due_date": "2026-05-31"}],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=mock_col):
            mark_done("Test")
        updated_meta = mock_col.update.call_args[1]["metadatas"][0]
        assert updated_meta["entity_type"] == "event"
        assert updated_meta["due_date"] == "2026-05-31"
        assert updated_meta["mention_count"] == 3
