"""
Tests für Phase 121: MemoryUpdateResult – typisiertes Result-Objekt.
API: _ok/_invalid/_reject als Top-Level-Funktionen
Felder: success / updated_profile / allow_fallback / user_message
"""

import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage
from agent.agents.memory_agent import (
    _apply_memory_update, MemoryUpdateResult, _ok, _invalid, _reject,
)

@pytest.fixture
def empty_profile():
    return {}

@pytest.fixture
def full_profile():
    return {
        "identity": {"name": "Fabio", "location": "Berlin"},
        "people": [{"name": "Anna", "context": "Freundin"}],
        "projects": {"active": [{"name": "FabBot", "description": "Telegram Bot", "priority": "high"}]},
        "media": [{"title": "Star Trek", "type": "serie"}],
        "preferences": {"sprache": "deutsch"},
        "custom": [{"key": "motto", "value": "Keep it simple"}],
        "work": {"employer": "Freelance", "role": "AI Engineer"},
    }

class TestMemoryUpdateResult:
    def test_ok(self):
        r = _ok({"k": "v"})
        assert r.success is True
        assert r.updated_profile == {"k": "v"}
        assert r.allow_fallback is True
        assert r.user_message is None

    def test_invalid(self):
        r = _invalid()
        assert r.success is False
        assert r.allow_fallback is True
        assert r.updated_profile is None
        assert r.user_message is None

    def test_reject(self):
        r = _reject("Nicht möglich.")
        assert r.success is False
        assert r.allow_fallback is False
        assert r.user_message == "Nicht möglich."
        assert r.updated_profile is None

class TestBotInstructionDelete:
    def test_returns_reject(self, empty_profile):
        r = _apply_memory_update(empty_profile, "delete", "bot_instruction", {})
        assert r.success is False
        assert r.allow_fallback is False
        assert r.user_message is not None
        assert "claude.md" in r.user_message

    def test_no_profile_mutation(self, full_profile):
        import copy
        orig = copy.deepcopy(full_profile)
        r = _apply_memory_update(full_profile, "delete", "bot_instruction", {})
        assert r.updated_profile is None
        assert full_profile == orig

    def test_save_bot_instruction_invalid(self, empty_profile):
        r = _apply_memory_update(empty_profile, "save", "bot_instruction", {"text": "x"})
        assert r.success is False
        assert r.allow_fallback is True

class TestProjectDelete:
    def test_empty_name_reject(self, full_profile):
        r = _apply_memory_update(full_profile, "delete", "project", {"name": ""})
        assert r.success is False
        assert r.allow_fallback is False
        assert r.user_message is not None

    def test_valid_name_ok(self, full_profile):
        r = _apply_memory_update(full_profile, "delete", "project", {"name": "FabBot"})
        assert r.success is True
        active = r.updated_profile.get("projects", {}).get("active", [])
        assert not any(p.get("name") == "FabBot" for p in active)

    def test_no_match_ok(self, full_profile):
        r = _apply_memory_update(full_profile, "delete", "project", {"name": "NichtVorhanden"})
        assert r.success is True

class TestInvalidInputs:
    @pytest.mark.parametrize("cat,data", [
        ("people", {"name": ""}),
        ("place", {"name": ""}),
        ("media", {"title": ""}),
        ("job", {"employer": ""}),
        ("location", {"location": ""}),
        ("custom", {"key": "", "value": "x"}),
        ("custom", {"key": "x", "value": ""}),
    ])
    def test_invalid(self, empty_profile, cat, data):
        r = _apply_memory_update(empty_profile, "save", cat, data)
        assert r.success is False
        assert r.allow_fallback is True

    def test_unknown_action(self, empty_profile):
        r = _apply_memory_update(empty_profile, "unknown", "custom", {"key": "x", "value": "y"})
        assert r.success is False
        assert r.allow_fallback is True

class TestSuccess:
    def test_save_people(self, empty_profile):
        r = _apply_memory_update(empty_profile, "save", "people", {"name": "Bob", "context": "Kollege"})
        assert r.success is True
        assert any(p["name"] == "Bob" for p in r.updated_profile.get("people", []))

    def test_delete_media(self, full_profile):
        r = _apply_memory_update(full_profile, "delete", "media", {"title": "Star Trek"})
        assert r.success is True
        assert not any(m.get("title") == "Star Trek" for m in r.updated_profile.get("media", []))

    def test_no_mutation(self, full_profile):
        import copy
        orig = copy.deepcopy(full_profile)
        _apply_memory_update(full_profile, "save", "people", {"name": "Neu", "context": "Test"})
        assert full_profile == orig

class TestMemoryAgentIntegration:
    def _state(self, text):
        return {"messages": [HumanMessage(content=text)]}

    @pytest.mark.asyncio
    async def test_bot_instruction_delete_no_add_note(self):
        from agent.agents.memory_agent import memory_agent
        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mp,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mn,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mp.return_value = {"action": "delete", "category": "bot_instruction", "data": {"text": "x"}}
            r = await memory_agent(self._state("Lösch die Instruktion"))
            mn.assert_not_called()
            assert "claude.md" in r["messages"][0].content
            assert "🗑️" not in r["messages"][0].content

    @pytest.mark.asyncio
    async def test_project_empty_name_no_add_note(self):
        from agent.agents.memory_agent import memory_agent
        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mp,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mn,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mp.return_value = {"action": "delete", "category": "project", "data": {"name": ""}}
            r = await memory_agent(self._state("Lösch Projekt"))
            mn.assert_not_called()
            assert "🗑️" not in r["messages"][0].content

    @pytest.mark.asyncio
    async def test_invalid_save_uses_add_note(self):
        from agent.agents.memory_agent import memory_agent
        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mp,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mn,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mp.return_value = {"action": "save", "category": "people", "data": {"name": ""}}
            await memory_agent(self._state("Merke was"))
            mn.assert_called_once()
