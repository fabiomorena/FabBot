"""
tests/test_ph224_memory_extractor_noop.py – Phase 224 (Issue #285)

Der memory_agent Extractor crashte mit JSONDecodeError, wenn der Sonnet-Extractor
korrekt erkennt, dass es nichts zu speichern gibt (leere/Non-JSON-Antwort). Das
wurde als ERROR geloggt und endete in einer verwirrenden generischen Clarify-Frage.

Erwartet: leere/Non-JSON-Antwort → sauberes action=noop (kein ERROR), node gibt
eine ehrliche Antwort statt der generischen "etwas Bestimmtes merke"-Frage.
"""

import json
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage

from agent.agents.memory_agent import _extract_with_skill, memory_agent


def _llm_returning(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=msg)
    return llm


@pytest.mark.asyncio
class TestExtractorNoop:
    async def _extract(self, content: str) -> dict:
        msgs = [HumanMessage(content="Vergiss morgen nicht das Meeting um 10")]
        with patch("agent.agents.memory_agent.load_skill", return_value="dummy skill prompt"):
            with patch("agent.agents.memory_agent.get_llm", return_value=_llm_returning(content)):
                return await _extract_with_skill(msgs, "event", "save", None)

    async def test_empty_content_returns_noop(self):
        assert await self._extract("") == {"action": "noop"}

    async def test_whitespace_content_returns_noop(self):
        assert await self._extract("   \n  ") == {"action": "noop"}

    async def test_non_json_returns_noop(self):
        result = await self._extract("Das ist kein JSON")
        assert result["action"] == "noop"

    async def test_valid_json_still_passes_through(self):
        payload = json.dumps({"action": "save", "data": {"text": "x"}})
        result = await self._extract(payload)
        assert result["action"] == "save"
        assert result["data"] == {"text": "x"}

    async def test_empty_content_logs_no_error(self, caplog):
        with caplog.at_level(logging.DEBUG):
            await self._extract("")
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "agent.agents.memory_agent"]
        assert errors == [], f"Unerwartete ERROR-Logs: {[r.message for r in errors]}"


@pytest.mark.asyncio
class TestMemoryAgentNoopResponse:
    async def _run(self, parsed: dict) -> str:
        state = {"messages": [HumanMessage(content="Vergiss morgen nicht das Meeting um 10")]}
        with patch("agent.agents.memory_agent._parse_memory_intent", new=AsyncMock(return_value=parsed)):
            with patch("agent.agents.memory_agent.load_profile_with_hash", return_value=({}, "abc123")):
                r = await memory_agent(state)
        return r["messages"][0].content

    async def test_noop_avoids_generic_clarify(self):
        content = await self._run({"action": "noop", "category": "event", "data": {}})
        assert "etwas Bestimmtes merke" not in content

    async def test_noop_gives_honest_hint(self):
        content = await self._run({"action": "noop", "category": "event", "data": {}})
        assert "erinner" in content.lower()

    async def test_real_error_still_generic_clarify(self):
        # echter Systemfehler (action=error) behält das bisherige Verhalten
        content = await self._run({"action": "error"})
        assert "etwas Bestimmtes merke" in content
