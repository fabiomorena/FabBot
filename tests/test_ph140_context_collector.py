"""
tests/test_ph140_context_collector.py – Phase 140 (Issue #87)

Testet den Context Collector:
- Entitäten-Extraktion via LLM (Haiku)
- ChromaDB-Upsert mit korrektem Schema
- Duplikat-Erkennung via deterministischer ID
- Fehlertoleranz (LLM-Fehler, ChromaDB-Fehler → kein Crash)
- Async fire-and-forget verhält sich korrekt
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agent.proactive.collector import (
    _normalize_name,
    _entity_id,
    _parse_entities,
    collect_entities,
    ENTITY_TYPES,
)


class TestNormalizeName:
    def test_lowercase(self):
        assert _normalize_name("Steffi") == "steffi"

    def test_strips_whitespace(self):
        assert _normalize_name("  Berlin  ") == "berlin"

    def test_removes_special_chars(self):
        assert _normalize_name("São Paulo") == "são paulo"

    def test_empty_string(self):
        assert _normalize_name("") == ""


class TestEntityId:
    def test_deterministic(self):
        id1 = _entity_id("person", "steffi")
        id2 = _entity_id("person", "steffi")
        assert id1 == id2

    def test_different_types_different_id(self):
        assert _entity_id("person", "berlin") != _entity_id("place", "berlin")

    def test_is_sha256_hex(self):
        result = _entity_id("person", "test")
        expected = hashlib.sha256(b"person:test").hexdigest()
        assert result == expected


class TestParseEntities:
    def test_valid_json(self):
        raw = json.dumps(
            [
                {"type": "person", "name": "Steffi", "context": "Steffi feiert 70. Geburtstag"},
                {"type": "place", "name": "Salvador", "context": "Reise nach Salvador Ende Mai"},
            ]
        )
        entities = _parse_entities(raw)
        assert len(entities) == 2
        assert entities[0]["name"] == "Steffi"
        assert entities[1]["type"] == "place"

    def test_invalid_json_returns_empty(self):
        entities = _parse_entities("das ist kein json")
        assert entities == []

    def test_unknown_type_filtered_out(self):
        raw = json.dumps(
            [
                {"type": "unknown_type", "name": "X", "context": "..."},
                {"type": "person", "name": "Max", "context": "Max ist ein Freund"},
            ]
        )
        entities = _parse_entities(raw)
        assert len(entities) == 1
        assert entities[0]["name"] == "Max"

    def test_missing_fields_filtered_out(self):
        raw = json.dumps(
            [
                {"type": "person"},  # name fehlt
                {"name": "Max"},  # type fehlt
                {"type": "person", "name": "Lena", "context": "Lena ist meine Schwester"},
            ]
        )
        entities = _parse_entities(raw)
        assert len(entities) == 1

    def test_empty_list(self):
        assert _parse_entities("[]") == []

    def test_markdown_json_block(self):
        raw = '```json\n[{"type": "person", "name": "Anna", "context": "Anna ist neu"}]\n```'
        entities = _parse_entities(raw)
        assert len(entities) == 1
        assert entities[0]["name"] == "Anna"


class TestCollectEntities:
    async def test_extracts_and_upserts(self):
        llm_response = json.dumps(
            [{"type": "person", "name": "Steffi", "context": "Steffi feiert 70. Geburtstag Ende Mai"}]
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=llm_response)

        mock_collection = MagicMock()

        with (
            patch("agent.proactive.collector._get_llm", return_value=mock_llm),
            patch("agent.proactive.collector._get_entities_collection", return_value=mock_collection),
        ):
            await collect_entities(
                user_message="Steffi feiert bald 70.", bot_response="Schön, magst du ihr etwas schenken?"
            )

        assert mock_collection.upsert.called
        call_kwargs = mock_collection.upsert.call_args[1]
        assert "Steffi" in call_kwargs["documents"][0]
        assert call_kwargs["metadatas"][0]["entity_type"] == "person"
        assert call_kwargs["metadatas"][0]["status"] == "open"

    async def test_llm_error_does_not_raise(self):
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM down")

        with (
            patch("agent.proactive.collector._get_llm", return_value=mock_llm),
            patch("agent.proactive.collector._get_entities_collection", return_value=MagicMock()),
        ):
            await collect_entities(user_message="Hi", bot_response="Hey")
        # Kein Exception

    async def test_chromadb_error_does_not_raise(self):
        llm_response = json.dumps([{"type": "task", "name": "Urlaub planen", "context": "Urlaub Ende Mai"}])
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=llm_response)

        mock_collection = MagicMock()
        mock_collection.upsert.side_effect = Exception("ChromaDB down")

        with (
            patch("agent.proactive.collector._get_llm", return_value=mock_llm),
            patch("agent.proactive.collector._get_entities_collection", return_value=mock_collection),
        ):
            await collect_entities(user_message="Urlaub planen", bot_response="Wohin?")
        # Kein Exception

    async def test_empty_message_skipped(self):
        mock_llm = AsyncMock()
        with patch("agent.proactive.collector._get_llm", return_value=mock_llm):
            await collect_entities(user_message="", bot_response="")
        assert not mock_llm.ainvoke.called

    async def test_upsert_uses_deterministic_id(self):
        llm_response = json.dumps([{"type": "person", "name": "Max", "context": "Max ist mein Bruder"}])
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=llm_response)
        mock_collection = MagicMock()

        with (
            patch("agent.proactive.collector._get_llm", return_value=mock_llm),
            patch("agent.proactive.collector._get_entities_collection", return_value=mock_collection),
        ):
            await collect_entities(user_message="Max ruft an", bot_response="Ok")

        ids = mock_collection.upsert.call_args[1]["ids"]
        expected_id = _entity_id("person", "max")
        assert ids == [expected_id]


class TestEntityTypes:
    def test_valid_types_defined(self):
        assert "person" in ENTITY_TYPES
        assert "place" in ENTITY_TYPES
        assert "event" in ENTITY_TYPES
        assert "task" in ENTITY_TYPES
        assert "intent" not in ENTITY_TYPES  # intent → intent_extractor.py
