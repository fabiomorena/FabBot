"""
Tests für Phase 121: MemoryUpdateResult – typisiertes Result-Objekt.

Testet:
1. MemoryUpdateResult Semantik für alle drei Outcomes
2. bot_instruction delete → _reject (allow_fallback=False, user_message gesetzt)
3. project delete mit leerem name → _reject
4. Ungültige Eingaben → _invalid (allow_fallback=True)
5. Erfolgreiche Operationen → _ok (success=True, updated_profile gesetzt)
6. memory_agent() Caller-Logik: kein add_note_to_profile bei _reject
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage

# Importpfad anpassen je nach Projektstruktur
from agent.agents.memory_agent import (
    _apply_memory_update,
    MemoryUpdateResult,
    _ok,
    _invalid,
    _reject,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_profile():
    return {}


@pytest.fixture
def full_profile():
    return {
        "identity": {"name": "Fabio", "location": "Berlin"},
        "people": [{"name": "Anna", "context": "Freundin"}],
        "projects": {"active": [{"name": "FabBot", "description": "Telegram Bot", "priority": "high"}]},
        "places": [{"name": "Berghain", "type": "club", "location": "Berlin"}],
        "media": [{"title": "Star Trek", "type": "serie"}],
        "preferences": {
            "entertainment": {"favorite_series": "Star Trek"},
            "tech": {"editor": "Neovim"},
        },
        "custom": [{"key": "motto", "value": "Keep it simple"}],
        "work": {"employer": "Freelance", "role": "AI Engineer"},
    }


# ---------------------------------------------------------------------------
# MemoryUpdateResult: Datenklasse
# ---------------------------------------------------------------------------

class TestMemoryUpdateResult:
    def test_ok_sets_success_true(self):
        profile = {"key": "value"}
        result = _ok(profile)
        assert result.success is True
        assert result.updated_profile == profile
        assert result.allow_fallback is True  # Default, irrelevant bei success=True
        assert result.user_message is None

    def test_invalid_sets_success_false_with_fallback(self):
        result = _invalid()
        assert result.success is False
        assert result.allow_fallback is True
        assert result.updated_profile is None
        assert result.user_message is None

    def test_reject_sets_success_false_no_fallback(self):
        result = _reject("Nicht möglich.")
        assert result.success is False
        assert result.allow_fallback is False
        assert result.user_message == "Nicht möglich."
        assert result.updated_profile is None


# ---------------------------------------------------------------------------
# _apply_memory_update: bot_instruction delete
# ---------------------------------------------------------------------------

class TestApplyMemoryUpdateBotInstruction:
    def test_delete_bot_instruction_returns_reject(self, empty_profile):
        result = _apply_memory_update(empty_profile, "delete", "bot_instruction", {"text": "Sei kurz."})
        assert isinstance(result, MemoryUpdateResult)
        assert result.success is False
        assert result.allow_fallback is False
        assert result.user_message is not None
        assert "claude.md" in result.user_message

    def test_delete_bot_instruction_does_not_modify_profile(self, full_profile):
        import copy
        original = copy.deepcopy(full_profile)
        result = _apply_memory_update(full_profile, "delete", "bot_instruction", {})
        assert result.success is False
        assert result.updated_profile is None
        # Originalprofil unverändert
        assert full_profile == original

    def test_save_bot_instruction_not_handled_here(self, empty_profile):
        # save/update bot_instruction wird im Caller (memory_agent) vor _apply_memory_update behandelt
        # _apply_memory_update hat keinen save-Branch für bot_instruction → _invalid()
        result = _apply_memory_update(empty_profile, "save", "bot_instruction", {"text": "Sei kurz."})
        assert isinstance(result, MemoryUpdateResult)
        assert result.success is False
        assert result.allow_fallback is True  # Fallback erlaubt (unbekannte category)


# ---------------------------------------------------------------------------
# _apply_memory_update: project delete mit leerem name
# ---------------------------------------------------------------------------

class TestApplyMemoryUpdateProjectDelete:
    def test_delete_project_empty_name_returns_reject(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "project", {"name": ""})
        assert result.success is False
        assert result.allow_fallback is False
        assert result.user_message is not None

    def test_delete_project_valid_name_returns_ok(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "project", {"name": "FabBot"})
        assert result.success is True
        assert result.updated_profile is not None
        active = result.updated_profile.get("projects", {}).get("active", [])
        assert not any(p.get("name") == "FabBot" for p in active)

    def test_delete_project_no_match_returns_ok_unchanged(self, full_profile):
        # Kein Match ist kein Fehler – Profil bleibt unverändert, aber success=True
        result = _apply_memory_update(full_profile, "delete", "project", {"name": "NichtVorhanden"})
        assert result.success is True


# ---------------------------------------------------------------------------
# _apply_memory_update: ungültige Eingaben → _invalid
# ---------------------------------------------------------------------------

class TestApplyMemoryUpdateInvalidInputs:
    @pytest.mark.parametrize("category,data", [
        ("people", {"name": "", "context": "x"}),
        ("place", {"name": ""}),
        ("media", {"title": ""}),
        ("preference", {"key": "", "value": "x"}),
        ("preference", {"key": "x", "value": ""}),
        ("job", {"employer": ""}),
        ("location", {"location": ""}),
        ("custom", {"key": "", "value": "x"}),
        ("custom", {"key": "x", "value": ""}),
    ])
    def test_invalid_input_returns_invalid(self, empty_profile, category, data):
        result = _apply_memory_update(empty_profile, "save", category, data)
        assert result.success is False
        assert result.allow_fallback is True

    def test_unknown_action_returns_invalid(self, empty_profile):
        result = _apply_memory_update(empty_profile, "unknown_action", "custom", {"key": "x", "value": "y"})
        assert result.success is False
        assert result.allow_fallback is True

    def test_unknown_category_returns_invalid(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "unknown_category", {"key": "x"})
        assert result.success is False
        assert result.allow_fallback is True


# ---------------------------------------------------------------------------
# _apply_memory_update: erfolgreiche Operationen → _ok
# ---------------------------------------------------------------------------

class TestApplyMemoryUpdateSuccess:
    def test_save_people_returns_ok(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "people", {"name": "Bob", "context": "Kollege"})
        assert result.success is True
        assert result.updated_profile is not None
        assert any(p["name"] == "Bob" for p in result.updated_profile.get("people", []))

    def test_save_preference_nested_returns_ok(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "preference", {
            "key": "favorite_series", "value": "Star Trek", "subcategory": "entertainment"
        })
        assert result.success is True
        prefs = result.updated_profile.get("preferences", {})
        assert prefs.get("entertainment", {}).get("favorite_series") == "Star Trek"

    def test_delete_preference_by_key_returns_ok(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "preference", {"key": "editor"})
        assert result.success is True
        prefs = result.updated_profile.get("preferences", {})
        tech = prefs.get("tech", {})
        assert "editor" not in tech

    def test_delete_preference_by_value_returns_ok(self, full_profile):
        # "Star Trek" ist ein Wert, kein Key – Phase 119 Wert-basierte Suche
        result = _apply_memory_update(full_profile, "delete", "preference", {"key": "star trek"})
        assert result.success is True
        prefs = result.updated_profile.get("preferences", {})
        entertainment = prefs.get("entertainment", {})
        assert "favorite_series" not in entertainment

    def test_delete_media_returns_ok(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "media", {"title": "Star Trek"})
        assert result.success is True
        media = result.updated_profile.get("media", [])
        assert not any(m.get("title") == "Star Trek" for m in media)

    def test_save_does_not_mutate_original_profile(self, full_profile):
        import copy
        original = copy.deepcopy(full_profile)
        _apply_memory_update(full_profile, "save", "people", {"name": "Neu", "context": "Test"})
        assert full_profile == original  # deepcopy in _apply_memory_update schützt Original


# ---------------------------------------------------------------------------
# memory_agent() Caller: Integration mit MemoryUpdateResult
# ---------------------------------------------------------------------------

class TestMemoryAgentCaller:
    """
    Testet dass memory_agent() korrekt auf MemoryUpdateResult reagiert.
    Alle externen Abhängigkeiten werden gemockt.
    """

    def _make_state(self, text: str) -> dict:
        return {
            "messages": [HumanMessage(content=text)],
            "last_agent_result": None,
            "last_agent_name": None,
        }

    @pytest.mark.asyncio
    async def test_bot_instruction_delete_no_add_note_called(self):
        """
        Bei bot_instruction delete darf add_note_to_profile NICHT aufgerufen werden.
        User bekommt die explizite Fehlermeldung aus _reject().
        """
        from agent.agents.memory_agent import memory_agent

        state = self._make_state("Lösch deine Bot-Instruktion")

        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mock_parse,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mock_note,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mock_parse.return_value = {
                "action": "delete",
                "category": "bot_instruction",
                "data": {"text": "Sei kurz."},
            }

            result = await memory_agent(state)

            # add_note_to_profile darf nicht aufgerufen worden sein
            mock_note.assert_not_called()

            # User bekommt die korrekte Meldung
            response_text = result["messages"][0].content
            assert "claude.md" in response_text
            assert "🗑️" not in response_text  # Kein falsches Gelöscht-Feedback

    @pytest.mark.asyncio
    async def test_project_delete_empty_name_no_add_note_called(self):
        """
        Bei project delete mit leerem name darf add_note_to_profile NICHT aufgerufen werden.
        """
        from agent.agents.memory_agent import memory_agent

        state = self._make_state("Lösch mein Projekt")

        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mock_parse,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mock_note,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mock_parse.return_value = {
                "action": "delete",
                "category": "project",
                "data": {"name": ""},
            }

            result = await memory_agent(state)

            mock_note.assert_not_called()
            response_text = result["messages"][0].content
            assert "🗑️" not in response_text

    @pytest.mark.asyncio
    async def test_invalid_input_falls_back_to_add_note(self):
        """
        Bei ungültiger Eingabe (allow_fallback=True) SOLL add_note_to_profile aufgerufen werden.
        """
        from agent.agents.memory_agent import memory_agent

        state = self._make_state("Merke dir irgendwas")

        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock) as mock_parse,
            patch("agent.agents.memory_agent.add_note_to_profile", new_callable=AsyncMock) as mock_note,
            patch("agent.agents.memory_agent.write_profile", new_callable=AsyncMock),
        ):
            mock_parse.return_value = {
                "action": "save",
                "category": "people",
                "data": {"name": ""},  # Leerer Name → _invalid()
            }

            await memory_agent(state)

            mock_note.assert_called_once()
