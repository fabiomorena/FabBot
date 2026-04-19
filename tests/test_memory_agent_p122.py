"""Tests Phase 122 – bot_instruction delete Pre-Routing (Issue #52)."""
import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage
from agent.agents.memory_agent import _apply_memory_update, MemoryUpdateResult, _ok, _invalid, _reject, memory_agent

class TestBotInstructionDeleteApply:
    def test_returns_reject(self):
        r = _apply_memory_update({}, "delete", "bot_instruction", {"text": "x"})
        assert r.success is False
        assert r.allow_fallback is False
        assert r.user_message is not None
        assert "claude.md" in r.user_message

    def test_profile_none(self):
        r = _apply_memory_update({}, "delete", "bot_instruction", {"text": "x"})
        assert r.updated_profile is None

@pytest.mark.asyncio
class TestBotInstructionDeleteAgent:
    async def _run(self, text):
        state = {"messages": [HumanMessage(content=text)]}
        parsed = {"action": "delete", "category": "bot_instruction", "data": {"text": text}}
        with patch("agent.agents.memory_agent._parse_memory_intent", new=AsyncMock(return_value=parsed)):
            with patch("agent.agents.memory_agent.load_profile", return_value={}):
                r = await memory_agent(state)
        return r["messages"][0].content

    async def test_claude_md_in_response(self):
        assert "claude.md" in await self._run("Vergiss die Instruktion")

    async def test_no_geloescht(self):
        assert "🗑️" not in await self._run("Lösch die Instruktion")

    async def test_no_write(self):
        state = {"messages": [HumanMessage(content="x")]}
        parsed = {"action": "delete", "category": "bot_instruction", "data": {"text": "x"}}
        with patch("agent.agents.memory_agent._parse_memory_intent", new=AsyncMock(return_value=parsed)):
            with patch("agent.agents.memory_agent.load_profile", return_value={}):
                with patch("agent.agents.memory_agent.write_profile", new=AsyncMock()) as mw:
                    await memory_agent(state)
                    mw.assert_not_called()

    async def test_no_add_note(self):
        state = {"messages": [HumanMessage(content="x")]}
        parsed = {"action": "delete", "category": "bot_instruction", "data": {"text": "x"}}
        with patch("agent.agents.memory_agent._parse_memory_intent", new=AsyncMock(return_value=parsed)):
            with patch("agent.agents.memory_agent.load_profile", return_value={}):
                with patch("agent.agents.memory_agent.add_note_to_profile", new=AsyncMock()) as mn:
                    await memory_agent(state)
                    mn.assert_not_called()

@pytest.mark.asyncio
class TestSaveRegression:
    async def test_save_still_works(self):
        state = {"messages": [HumanMessage(content="Merke dir grundsätzlich: auf Deutsch")]}
        parsed = {"action": "save", "category": "bot_instruction", "data": {"text": "Auf Deutsch antworten"}}
        import agent.agents.memory_agent as m
        import agent.claude_md as c
        with patch.object(m, "_parse_memory_intent", new=AsyncMock(return_value=parsed)):
            with patch.object(c, "append_to_claude_md", new=AsyncMock(return_value=True)):
                r = await memory_agent(state)
        assert "nicht automatisch gelöscht" not in r["messages"][0].content

class TestSupervisorPreRouting:
    def test_prefixes_defined(self):
        from agent.supervisor import _BOT_INSTRUCTION_DELETE_PREFIXES
        assert len(_BOT_INSTRUCTION_DELETE_PREFIXES) > 0

    def test_vergiss_die_instruktion(self):
        from agent.supervisor import _BOT_INSTRUCTION_DELETE_PREFIXES
        assert any("vergiss die instruktion über x".startswith(p) for p in _BOT_INSTRUCTION_DELETE_PREFIXES)

    def test_loesche_die_instruktion(self):
        from agent.supervisor import _BOT_INSTRUCTION_DELETE_PREFIXES
        assert any("lösche die instruktion".startswith(p) for p in _BOT_INSTRUCTION_DELETE_PREFIXES)

    def test_vergiss_person_not_matched(self):
        from agent.supervisor import _BOT_INSTRUCTION_DELETE_PREFIXES
        assert not any("vergiss max mustermann".startswith(p) for p in _BOT_INSTRUCTION_DELETE_PREFIXES)

    def test_order_in_source(self):
        import inspect
        from agent import supervisor
        src = inspect.getsource(supervisor.supervisor_node)
        bot = src.find("_BOT_INSTRUCTION_DELETE_PREFIXES")
        first = src.find("_MEMORY_DELETE_PREFIXES")
        second = src.find("_MEMORY_DELETE_PREFIXES", first + 1)
        mem = second if second != -1 else first
        assert 0 < bot < mem

class TestDeleteRegression:
    def test_people(self):
        r = _apply_memory_update({"people": [{"name": "Max", "context": "x"}]}, "delete", "people", {"name": "Max"})
        assert r.success and r.updated_profile["people"] == []

    def test_custom(self):
        r = _apply_memory_update({"custom": [{"key": "k", "value": "v"}]}, "delete", "custom", {"key": "k"})
        assert r.success and r.updated_profile["custom"] == []
