"""
Tests für agent/proactive/intent_extractor.py – Phase 162 (Issue #107)
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# _parse_intentions
# ---------------------------------------------------------------------------


class TestParseIntentions:
    def setup_method(self):
        from agent.proactive.intent_extractor import _parse_intentions

        self.parse = _parse_intentions

    def test_valid_intent(self):
        raw = json.dumps([{"name": "Steuererklärung", "context": "ich muss die Steuererklärung machen"}])
        result = self.parse(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Steuererklärung"

    def test_intent_with_due_date(self):
        raw = json.dumps([{"name": "Arzt-Termin", "context": "morgen Arzt", "due_date": "2026-04-28"}])
        result = self.parse(raw)
        assert result[0]["due_date"] == "2026-04-28"

    def test_empty_array(self):
        assert self.parse("[]") == []

    def test_missing_name_skipped(self):
        raw = json.dumps([{"context": "etwas"}])
        assert self.parse(raw) == []

    def test_missing_context_skipped(self):
        raw = json.dumps([{"name": "X"}])
        assert self.parse(raw) == []

    def test_markdown_codeblock_stripped(self):
        raw = '```json\n[{"name": "X", "context": "Y"}]\n```'
        result = self.parse(raw)
        assert len(result) == 1

    def test_invalid_json_returns_empty(self):
        assert self.parse("kein json") == []

    def test_non_list_returns_empty(self):
        assert self.parse('{"name": "X"}') == []


# ---------------------------------------------------------------------------
# _intent_id
# ---------------------------------------------------------------------------


class TestIntentId:
    def setup_method(self):
        from agent.proactive.intent_extractor import _intent_id

        self.intent_id = _intent_id

    def test_deterministic(self):
        assert self.intent_id("Steuererklärung") == self.intent_id("Steuererklärung")

    def test_case_insensitive(self):
        assert self.intent_id("STEUERN") == self.intent_id("steuern")

    def test_different_names_differ(self):
        assert self.intent_id("A") != self.intent_id("B")

    def test_returns_string(self):
        assert isinstance(self.intent_id("X"), str)


# ---------------------------------------------------------------------------
# extract_intentions – fire-and-forget
# ---------------------------------------------------------------------------


class TestExtractIntentions:
    def _make_llm_response(self, intentions: list) -> AsyncMock:
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(intentions)
        mock_llm.ainvoke.return_value = mock_response
        return mock_llm

    @pytest.mark.asyncio
    async def test_empty_message_skips_llm(self):
        from agent.proactive.intent_extractor import extract_intentions

        with patch("agent.proactive.intent_extractor._get_llm") as mock_get:
            await extract_intentions("   ")
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_intentions_no_upsert(self):
        from agent.proactive.intent_extractor import extract_intentions

        mock_llm = self._make_llm_response([])
        mock_collection = MagicMock()
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=mock_collection),
        ):
            await extract_intentions("schöner Tag heute")
        mock_collection.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_intention_saved_to_chroma(self):
        from agent.proactive.intent_extractor import extract_intentions

        intentions = [{"name": "Steuererklärung", "context": "ich muss die Steuererklärung machen"}]
        mock_llm = self._make_llm_response(intentions)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=mock_collection),
        ):
            await extract_intentions("ich muss die Steuererklärung machen")
        mock_collection.upsert.assert_called_once()
        call_kwargs = mock_collection.upsert.call_args
        metadatas = call_kwargs[1]["metadatas"] if call_kwargs[1] else call_kwargs[0][2]
        assert metadatas[0]["entity_type"] == "intent"
        assert metadatas[0]["status"] == "open"

    @pytest.mark.asyncio
    async def test_due_date_stored_when_present(self):
        from agent.proactive.intent_extractor import extract_intentions

        intentions = [{"name": "Arzt", "context": "morgen Arzt", "due_date": "2026-04-28"}]
        mock_llm = self._make_llm_response(intentions)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=mock_collection),
        ):
            await extract_intentions("morgen Arzt")
        call_kwargs = mock_collection.upsert.call_args
        metadatas = call_kwargs[1]["metadatas"] if call_kwargs[1] else call_kwargs[0][2]
        assert metadatas[0].get("due_date") == "2026-04-28"

    @pytest.mark.asyncio
    async def test_mention_count_incremented_on_duplicate(self):
        from agent.proactive.intent_extractor import extract_intentions

        intentions = [{"name": "Sport", "context": "ich sollte mehr Sport machen"}]
        mock_llm = self._make_llm_response(intentions)
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["existing_id"],
            "metadatas": [{"mention_count": 3}],
        }
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=mock_collection),
        ):
            await extract_intentions("ich sollte mehr Sport machen")
        call_kwargs = mock_collection.upsert.call_args
        metadatas = call_kwargs[1]["metadatas"] if call_kwargs[1] else call_kwargs[0][2]
        assert metadatas[0]["mention_count"] == 4

    @pytest.mark.asyncio
    async def test_chroma_unavailable_does_not_raise(self):
        from agent.proactive.intent_extractor import extract_intentions

        intentions = [{"name": "X", "context": "Y"}]
        mock_llm = self._make_llm_response(intentions)
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=None),
        ):
            await extract_intentions("ich muss X machen")

    @pytest.mark.asyncio
    async def test_llm_exception_does_not_raise(self):
        from agent.proactive.intent_extractor import extract_intentions

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("LLM down")
        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=mock_llm),
            patch("agent.proactive.intent_extractor._get_collection", return_value=MagicMock()),
        ):
            await extract_intentions("ich muss X machen")
