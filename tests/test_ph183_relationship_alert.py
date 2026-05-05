"""
tests/test_ph183_relationship_alert.py – Phase 183 (Issue #108)

Testet den Beziehungs-Alert:
- _should_alert: Cooldown-Logik
- _query_unmentioned: ChromaDB-Query-Struktur + Ergebnis-Verarbeitung
- find_unmentioned_entities: Schwellwerte, Sortierung, MAX_ALERTS_PER_RUN, Fail-safe
- mark_alerted: Anti-Spam-Marker setzen
"""

import time
from unittest.mock import MagicMock, patch

from agent.proactive.relationship_alert import (
    ALERT_COOLDOWN_DAYS,
    MAX_ALERTS_PER_RUN,
    OTHER_THRESHOLD_DAYS,
    PERSON_THRESHOLD_DAYS,
    _query_unmentioned,
    _should_alert,
    find_unmentioned_entities,
    mark_alerted,
)


def _now() -> float:
    return time.time()


def _make_meta(
    entity_type: str,
    name: str,
    days_ago: float,
    now: float,
    last_alerted_days_ago: float | None = None,
) -> dict:
    ts = now - days_ago * 86400
    meta = {
        "entity_type": entity_type,
        "name": name,
        "source_context": f"Kontext zu {name}",
        "last_mentioned_at": "2026-01-01T00:00:00+00:00",
        "last_mentioned_at_ts": ts,
    }
    if last_alerted_days_ago is not None:
        meta["last_alerted_at_ts"] = now - last_alerted_days_ago * 86400
    return meta


class TestShouldAlert:
    def test_no_last_alerted_returns_true(self):
        assert _should_alert({}, _now()) is True

    def test_recent_alert_returns_false(self):
        now = _now()
        meta = {"last_alerted_at_ts": now - (ALERT_COOLDOWN_DAYS * 86400 - 3600)}
        assert _should_alert(meta, now) is False

    def test_old_alert_returns_true(self):
        now = _now()
        meta = {"last_alerted_at_ts": now - (ALERT_COOLDOWN_DAYS * 86400 + 3600)}
        assert _should_alert(meta, now) is True

    def test_exactly_at_boundary_returns_true(self):
        now = _now()
        meta = {"last_alerted_at_ts": now - ALERT_COOLDOWN_DAYS * 86400}
        assert _should_alert(meta, now) is True

    def test_invalid_value_returns_true(self):
        assert _should_alert({"last_alerted_at_ts": "invalid"}, _now()) is True

    def test_none_value_returns_true(self):
        assert _should_alert({"last_alerted_at_ts": None}, _now()) is True


class TestQueryUnmentioned:
    def test_correct_where_clause_for_person(self):
        now = _now()
        cutoff = now - PERSON_THRESHOLD_DAYS * 86400
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        _query_unmentioned(col, "person", cutoff, now)
        where = col.get.call_args.kwargs["where"]
        assert {"entity_type": {"$eq": "person"}} in where["$and"]
        assert {"last_mentioned_at_ts": {"$lt": cutoff}} in where["$and"]

    def test_returns_structured_trigger_item(self):
        now = _now()
        ts = now - 20 * 86400
        meta = {
            "name": "Steffi",
            "source_context": "Steffis Geburtstag",
            "last_mentioned_at": "2026-01-01T00:00:00+00:00",
            "last_mentioned_at_ts": ts,
        }
        col = MagicMock()
        col.get.return_value = {"ids": ["id1"], "metadatas": [meta]}
        results = _query_unmentioned(col, "person", ts + 1, now)
        assert len(results) == 1
        item = results[0]
        assert item["trigger_type"] == "relationship_alert"
        assert item["entity_type"] == "person"
        assert item["name"] == "Steffi"
        assert item["days_since_mention"] == 20
        assert item["id"] == "id1"

    def test_anti_spam_suppresses_recently_alerted(self):
        now = _now()
        ts = now - 20 * 86400
        meta = {
            "name": "Marco",
            "source_context": "ctx",
            "last_mentioned_at": "2026-01-01T00:00:00+00:00",
            "last_mentioned_at_ts": ts,
            "last_alerted_at_ts": now - 3600,
        }
        col = MagicMock()
        col.get.return_value = {"ids": ["id1"], "metadatas": [meta]}
        results = _query_unmentioned(col, "person", ts + 1, now)
        assert results == []

    def test_chromadb_exception_returns_empty(self):
        col = MagicMock()
        col.get.side_effect = Exception("DB-Fehler")
        results = _query_unmentioned(col, "person", 0.0, _now())
        assert results == []

    def test_empty_collection_returns_empty(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        results = _query_unmentioned(col, "person", 0.0, _now())
        assert results == []


class TestFindUnmentionedEntities:
    def _make_col_with_side_effects(self, *per_call_results):
        col = MagicMock()
        col.get.side_effect = list(per_call_results)
        return col

    def test_person_found_when_above_threshold(self):
        now = _now()
        meta = _make_meta("person", "Steffi", PERSON_THRESHOLD_DAYS + 1, now)
        col = self._make_col_with_side_effects(
            {"ids": ["id1"], "metadatas": [meta]},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            results = find_unmentioned_entities(now_ts=now)
        assert len(results) == 1
        assert results[0]["name"] == "Steffi"

    def test_other_type_found_when_above_30d_threshold(self):
        now = _now()
        meta = _make_meta("place", "Salvador", OTHER_THRESHOLD_DAYS + 1, now)
        col = self._make_col_with_side_effects(
            {"ids": [], "metadatas": []},
            {"ids": ["id2"], "metadatas": [meta]},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            results = find_unmentioned_entities(now_ts=now)
        assert any(r["name"] == "Salvador" for r in results)

    def test_collection_unavailable_returns_empty(self):
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=None):
            assert find_unmentioned_entities() == []

    def test_exception_returns_empty(self):
        with patch("agent.proactive.relationship_alert._get_entities_collection", side_effect=Exception("Crash")):
            assert find_unmentioned_entities() == []

    def test_max_alerts_per_run_respected(self):
        now = _now()
        count = MAX_ALERTS_PER_RUN + 2
        ids = [f"id{i}" for i in range(count)]
        metadatas = [
            _make_meta("person", f"Person{i}", PERSON_THRESHOLD_DAYS + 1 + i, now)
            for i in range(count)
        ]
        # Alle kommen von der person-Query, andere Queries geben nichts zurück
        col = self._make_col_with_side_effects(
            {"ids": ids, "metadatas": metadatas},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            results = find_unmentioned_entities(now_ts=now)
        assert len(results) <= MAX_ALERTS_PER_RUN

    def test_sorted_by_days_descending(self):
        now = _now()
        meta_newer = _make_meta("person", "Newer", PERSON_THRESHOLD_DAYS + 1, now)
        meta_older = _make_meta("person", "Older", PERSON_THRESHOLD_DAYS + 10, now)
        col = self._make_col_with_side_effects(
            {"ids": ["id1", "id2"], "metadatas": [meta_newer, meta_older]},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            results = find_unmentioned_entities(now_ts=now)
        assert results[0]["name"] == "Older"

    def test_days_since_mention_correct(self):
        now = _now()
        days_ago = PERSON_THRESHOLD_DAYS + 3
        meta = _make_meta("person", "Test", days_ago, now)
        col = self._make_col_with_side_effects(
            {"ids": ["id1"], "metadatas": [meta]},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            results = find_unmentioned_entities(now_ts=now)
        assert results[0]["days_since_mention"] == int(days_ago)

    def test_no_results_when_all_empty(self):
        col = self._make_col_with_side_effects(
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
            {"ids": [], "metadatas": []},
        )
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col), \
             patch("agent.proactive.relationship_alert._run_backfill_once"):
            assert find_unmentioned_entities(now_ts=_now()) == []


class TestMarkAlerted:
    def test_sets_last_alerted_at_ts(self):
        existing_meta = {"entity_type": "person", "name": "Steffi", "last_mentioned_at_ts": _now() - 86400 * 20}
        col = MagicMock()
        col.get.return_value = {"ids": ["id1"], "metadatas": [existing_meta]}
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col):
            mark_alerted("id1")
        col.update.assert_called_once()
        updated_meta = col.update.call_args.kwargs["metadatas"][0]
        assert "last_alerted_at_ts" in updated_meta
        assert isinstance(updated_meta["last_alerted_at_ts"], float)

    def test_preserves_existing_metadata(self):
        existing_meta = {"entity_type": "person", "name": "Steffi", "last_mentioned_at_ts": 1000.0}
        col = MagicMock()
        col.get.return_value = {"ids": ["id1"], "metadatas": [existing_meta]}
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col):
            mark_alerted("id1")
        updated_meta = col.update.call_args.kwargs["metadatas"][0]
        assert updated_meta["name"] == "Steffi"
        assert updated_meta["entity_type"] == "person"

    def test_collection_unavailable_no_crash(self):
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=None):
            mark_alerted("some-id")

    def test_entity_not_found_no_crash(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.relationship_alert._get_entities_collection", return_value=col):
            mark_alerted("non-existent")
        col.update.assert_not_called()

    def test_exception_no_crash(self):
        with patch("agent.proactive.relationship_alert._get_entities_collection", side_effect=Exception("Crash")):
            mark_alerted("id1")


class TestFallbackMessage:
    def test_relationship_alert_fallback(self):
        from agent.proactive.heartbeat import _fallback_message

        item = {"trigger_type": "relationship_alert", "name": "Steffi", "days_since_mention": 17}
        msg = _fallback_message(item)
        assert "Steffi" in msg
        assert "17" in msg

    def test_time_trigger_fallback(self):
        from agent.proactive.heartbeat import _fallback_message

        item = {"name": "Geburtstag", "days_until_due": 3}
        msg = _fallback_message(item)
        assert "Geburtstag" in msg
        assert "3" in msg
