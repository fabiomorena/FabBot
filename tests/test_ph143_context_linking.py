"""
tests/test_ph143_context_linking.py – Phase 143 (Issue #90)

Testet Context Linking:
- _link_id Deterministik und Reihenfolge-Unabhängigkeit
- link_entities: alle Paare werden verknüpft, Gewicht inkrementiert
- get_related_entities: sortiert nach Gewicht, fail-safe
- Collector ruft link_entities nach Upsert auf
"""

import hashlib
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


class TestLinkId:
    def test_deterministic(self):
        from agent.proactive.linker import _link_id
        assert _link_id("aaa", "bbb") == _link_id("aaa", "bbb")

    def test_order_independent(self):
        from agent.proactive.linker import _link_id
        assert _link_id("aaa", "bbb") == _link_id("bbb", "aaa")

    def test_different_pairs_different_ids(self):
        from agent.proactive.linker import _link_id
        assert _link_id("aaa", "bbb") != _link_id("aaa", "ccc")


class TestLinkEntities:
    def test_empty_list_no_op(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities([], {})
        assert not mock_col.upsert.called

    def test_single_entity_no_op(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1"], {"id1": "Steffi"})
        assert not mock_col.upsert.called

    def test_two_entities_one_link(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1", "id2"], {"id1": "Steffi", "id2": "Salvador"})
        assert mock_col.upsert.call_count == 1

    def test_three_entities_three_links(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1", "id2", "id3"], {"id1": "A", "id2": "B", "id3": "C"})
        assert mock_col.upsert.call_count == 3

    def test_weight_increments_on_second_call(self):
        from agent.proactive.linker import link_entities, _link_id
        lid = _link_id("id1", "id2")
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": [lid],
            "metadatas": [{"entity_id_a": "id1", "entity_id_b": "id2", "weight": 3}],
        }
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1", "id2"], {"id1": "A", "id2": "B"})
        upsert_meta = mock_col.upsert.call_args[1]["metadatas"][0]
        assert upsert_meta["weight"] == 4

    def test_no_collection_no_crash(self):
        from agent.proactive.linker import link_entities
        with patch("agent.proactive.linker._get_links_collection", return_value=None):
            link_entities(["id1", "id2"], {"id1": "A", "id2": "B"})

    def test_chromadb_error_no_crash(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        mock_col.get.side_effect = Exception("DB error")
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1", "id2"], {"id1": "A", "id2": "B"})

    def test_document_contains_both_names(self):
        from agent.proactive.linker import link_entities
        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_col):
            link_entities(["id1", "id2"], {"id1": "Steffi", "id2": "Salvador"})
        doc = mock_col.upsert.call_args[1]["documents"][0]
        assert "Steffi" in doc and "Salvador" in doc


class TestGetRelatedEntities:
    def test_returns_empty_if_no_links_collection(self):
        from agent.proactive.linker import get_related_entities
        with patch("agent.proactive.linker._get_links_collection", return_value=None):
            assert get_related_entities("id1") == []

    def test_returns_empty_if_no_links(self):
        from agent.proactive.linker import get_related_entities
        mock_links = MagicMock()
        mock_links.get.return_value = {"ids": [], "metadatas": []}
        mock_entities = MagicMock()
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_links), \
             patch("agent.proactive.linker._get_entities_collection", return_value=mock_entities):
            assert get_related_entities("id1") == []

    def test_returns_related_entity_sorted_by_weight(self):
        from agent.proactive.linker import get_related_entities
        mock_links = MagicMock()
        mock_links.get.return_value = {
            "ids": ["link1", "link2"],
            "metadatas": [
                {"entity_id_a": "id1", "entity_id_b": "id2", "weight": 5},
                {"entity_id_a": "id3", "entity_id_b": "id1", "weight": 2},
            ],
        }
        mock_entities = MagicMock()
        mock_entities.get.return_value = {
            "ids": ["id2", "id3"],
            "metadatas": [
                {"entity_type": "place", "name": "Salvador"},
                {"entity_type": "event", "name": "Geburtstag"},
            ],
        }
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_links), \
             patch("agent.proactive.linker._get_entities_collection", return_value=mock_entities):
            result = get_related_entities("id1")
        assert result[0]["name"] == "Salvador"
        assert result[0]["link_weight"] == 5
        assert result[1]["name"] == "Geburtstag"
        assert result[1]["link_weight"] == 2

    def test_link_weight_in_result(self):
        from agent.proactive.linker import get_related_entities
        mock_links = MagicMock()
        mock_links.get.return_value = {
            "ids": ["link1"],
            "metadatas": [{"entity_id_a": "id1", "entity_id_b": "id2", "weight": 7}],
        }
        mock_entities = MagicMock()
        mock_entities.get.return_value = {
            "ids": ["id2"],
            "metadatas": [{"entity_type": "person", "name": "Steffi"}],
        }
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_links), \
             patch("agent.proactive.linker._get_entities_collection", return_value=mock_entities):
            result = get_related_entities("id1")
        assert result[0]["link_weight"] == 7

    def test_chromadb_error_returns_empty(self):
        from agent.proactive.linker import get_related_entities
        mock_links = MagicMock()
        mock_links.get.side_effect = Exception("DB error")
        with patch("agent.proactive.linker._get_links_collection", return_value=mock_links), \
             patch("agent.proactive.linker._get_entities_collection", return_value=MagicMock()):
            assert get_related_entities("id1") == []


class TestCollectorCallsLinker:
    async def test_link_entities_called_after_upsert(self):
        from agent.proactive.collector import collect_entities
        llm_response = json.dumps([
            {"type": "person", "name": "Steffi", "context": "Steffi feiert Geburtstag"},
            {"type": "place", "name": "Salvador", "context": "Reise nach Salvador"},
        ])
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=llm_response)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}

        with patch("agent.proactive.collector._get_llm", return_value=mock_llm), \
             patch("agent.proactive.collector._get_entities_collection", return_value=mock_collection), \
             patch("agent.proactive.linker.link_entities") as mock_link:
            await collect_entities("Steffi reist nach Salvador", "Klingt toll!")

        assert mock_link.called
        call_ids = mock_link.call_args[0][0]
        assert len(call_ids) == 2

    async def test_link_entities_not_called_for_single_entity(self):
        from agent.proactive.collector import collect_entities
        llm_response = json.dumps([
            {"type": "person", "name": "Steffi", "context": "Steffi ist nett"},
        ])
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=llm_response)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}

        with patch("agent.proactive.collector._get_llm", return_value=mock_llm), \
             patch("agent.proactive.collector._get_entities_collection", return_value=mock_collection), \
             patch("agent.proactive.linker.link_entities") as mock_link:
            await collect_entities("Steffi ist nett", "Ja!")

        assert not mock_link.called
