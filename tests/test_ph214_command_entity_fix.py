"""
tests/test_ph214_command_entity_fix.py – Phase 214 (Issues #246, #247)

Testet die Fixes für das "Gustav-Problem":
- #247: collector filtert Kommando-Empfänger aus dem _EXTRACTION_PROMPT
- #246: relationship_alert ignoriert Einzel-Mention-Entitäten (MIN_MENTION_COUNT = 2)
"""

from unittest.mock import MagicMock

from agent.proactive.collector import _EXTRACTION_PROMPT
from agent.proactive.relationship_alert import (
    MIN_MENTION_COUNT,
    _query_unmentioned,
)


class TestCollectorCommandExclusion:
    def test_prompt_contains_command_exclusion(self):
        assert "Kommando" in _EXTRACTION_PROMPT or "kommando" in _EXTRACTION_PROMPT.lower()

    def test_prompt_excludes_recipient_patterns(self):
        assert "Empfänger" in _EXTRACTION_PROMPT or "Argument" in _EXTRACTION_PROMPT

    def test_prompt_mentions_sende_ruf_patterns(self):
        assert "Sende" in _EXTRACTION_PROMPT or "Ruf" in _EXTRACTION_PROMPT


class TestMinMentionCount:
    def test_min_mention_count_is_two(self):
        assert MIN_MENTION_COUNT == 2

    def test_query_unmentioned_includes_mention_count_filter(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        _query_unmentioned(col, "person", 0.0, 1.0)
        where = col.get.call_args.kwargs["where"]
        assert {"mention_count": {"$gte": MIN_MENTION_COUNT}} in where["$and"]

    def test_mention_count_filter_in_other_type_query(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        _query_unmentioned(col, "place", 0.0, 1.0)
        where = col.get.call_args.kwargs["where"]
        assert {"mention_count": {"$gte": MIN_MENTION_COUNT}} in where["$and"]

    def test_single_mention_entity_filtered_by_chromadb(self):
        """ChromaDB-Query enthält mention_count-Filter: Entitäten mit count=1 kommen gar nicht zurück."""
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        results = _query_unmentioned(col, "person", 0.0, 1.0)
        assert results == []
        where = col.get.call_args.kwargs["where"]
        conditions = where["$and"]
        mention_filter = next((c for c in conditions if "mention_count" in c), None)
        assert mention_filter is not None
        assert mention_filter["mention_count"]["$gte"] >= 2
