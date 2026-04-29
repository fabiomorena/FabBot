"""
Tests für Phase 119 – Nachhaltiges Preferences-System + profilbewusster Delete-Parser.
Closes #40.

Phase 121: Assertions auf MemoryUpdateResult umgestellt (result.updated_profile statt result[...]).
"""

import pytest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage

from agent.agents.memory_agent import (
    _flatten_profile_preferences,
    _build_profile_context_for_parser,
    _infer_subcategory,
    _apply_memory_update,
    _build_clarify_message,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_flat():
    """Altes flaches preferences-Format (Legacy)."""
    return {
        "preferences": {
            "favorite_fantasy_series": "Star Trek",
            "sport": "Laufen",
            "editor": "Neovim",
        },
        "media": [
            {"title": "Inception", "type": "film"},
        ],
        "people": [
            {"name": "Alice", "context": "Beste Freundin"},
        ],
    }


@pytest.fixture
def profile_nested():
    """Neues nested preferences-Format."""
    return {
        "preferences": {
            "entertainment": {
                "favorite_fantasy_series": "Star Trek",
                "favorite_film": "Inception",
            },
            "lifestyle": {
                "sport": "Laufen",
            },
            "tech": {
                "editor": "Neovim",
            },
        },
    }


@pytest.fixture
def profile_mixed():
    """Gemischtes Format: nested + flach."""
    return {
        "preferences": {
            "entertainment": {
                "favorite_fantasy_series": "Star Trek",
            },
            "lifestyle": {
                "sport": "Laufen",
            },
            "legacy_key": "alter_flacher_wert",  # flacher Legacy-Key
        },
    }


# ---------------------------------------------------------------------------
# _infer_subcategory
# ---------------------------------------------------------------------------


class TestInferSubcategory:
    def test_entertainment_serie(self):
        assert _infer_subcategory("favorite_fantasy_series", "Star Trek") == "entertainment"

    def test_entertainment_film(self):
        assert _infer_subcategory("lieblingsfilm", "Inception") == "entertainment"

    def test_lifestyle_sport(self):
        assert _infer_subcategory("sport", "Laufen") == "lifestyle"

    def test_tech_editor(self):
        assert _infer_subcategory("editor", "Neovim") == "tech"

    def test_tech_os(self):
        assert _infer_subcategory("betriebssystem", "macOS") == "tech"

    def test_fallback(self):
        assert _infer_subcategory("geburtsstadt", "München") == "persoenlich"

    def test_work_meeting(self):
        assert _infer_subcategory("meeting_rhythmus", "wöchentlich") == "work"


# ---------------------------------------------------------------------------
# _flatten_profile_preferences
# ---------------------------------------------------------------------------


class TestFlattenProfilePreferences:
    def test_flat_legacy(self, profile_flat):
        result = _flatten_profile_preferences(profile_flat)
        paths = [r[0] for r in result]
        assert "preferences.favorite_fantasy_series" in paths
        assert "preferences.sport" in paths
        assert "preferences.editor" in paths

    def test_flat_legacy_values(self, profile_flat):
        result = _flatten_profile_preferences(profile_flat)
        value_map = {r[1]: r[2] for r in result}
        assert value_map["favorite_fantasy_series"] == "Star Trek"
        assert value_map["sport"] == "Laufen"

    def test_nested(self, profile_nested):
        result = _flatten_profile_preferences(profile_nested)
        paths = [r[0] for r in result]
        assert "preferences.entertainment.favorite_fantasy_series" in paths
        assert "preferences.entertainment.favorite_film" in paths
        assert "preferences.lifestyle.sport" in paths
        assert "preferences.tech.editor" in paths

    def test_nested_values(self, profile_nested):
        result = _flatten_profile_preferences(profile_nested)
        value_map = {r[1]: r[2] for r in result}
        assert value_map["favorite_fantasy_series"] == "Star Trek"
        assert value_map["editor"] == "Neovim"

    def test_mixed(self, profile_mixed):
        result = _flatten_profile_preferences(profile_mixed)
        paths = [r[0] for r in result]
        assert "preferences.entertainment.favorite_fantasy_series" in paths
        assert "preferences.lifestyle.sport" in paths
        assert "preferences.legacy_key" in paths  # flacher Key bleibt

    def test_empty_profile(self):
        result = _flatten_profile_preferences({})
        assert result == []

    def test_no_preferences(self):
        result = _flatten_profile_preferences({"people": []})
        assert result == []


# ---------------------------------------------------------------------------
# _build_profile_context_for_parser
# ---------------------------------------------------------------------------


class TestBuildProfileContextForParser:
    def test_contains_preference_keys(self, profile_flat):
        ctx = _build_profile_context_for_parser(profile_flat)
        assert "favorite_fantasy_series" in ctx
        assert "Star Trek" in ctx

    def test_contains_nested_preference_keys(self, profile_nested):
        ctx = _build_profile_context_for_parser(profile_nested)
        assert "entertainment" in ctx
        assert "Star Trek" in ctx

    def test_contains_media(self, profile_flat):
        ctx = _build_profile_context_for_parser(profile_flat)
        assert "Inception" in ctx

    def test_contains_people(self, profile_flat):
        ctx = _build_profile_context_for_parser(profile_flat)
        assert "Alice" in ctx

    def test_max_length(self, profile_flat):
        ctx = _build_profile_context_for_parser(profile_flat)
        assert len(ctx) <= 900

    def test_empty_profile(self):
        ctx = _build_profile_context_for_parser({})
        assert ctx == ""


# ---------------------------------------------------------------------------
# _apply_memory_update – save preference nested
# Phase 121: result.updated_profile statt result[...]
# ---------------------------------------------------------------------------


class TestApplyMemoryUpdatePreferenceSave:
    def test_save_creates_nested_structure(self):
        profile = {"preferences": {}}
        result = _apply_memory_update(
            profile,
            "save",
            "preference",
            {"key": "favorite_fantasy_series", "value": "Star Trek", "subcategory": "entertainment"},
        )
        assert result.success is True
        assert result.updated_profile["preferences"]["entertainment"]["favorite_fantasy_series"] == "Star Trek"

    def test_save_infers_subcategory(self):
        profile = {"preferences": {}}
        result = _apply_memory_update(profile, "save", "preference", {"key": "sport", "value": "Laufen"})
        assert result.success is True
        assert result.updated_profile["preferences"]["lifestyle"]["sport"] == "Laufen"

    def test_save_adds_to_existing_subcategory(self):
        profile = {"preferences": {"entertainment": {"favorite_fantasy_series": "Star Trek"}}}
        result = _apply_memory_update(
            profile,
            "save",
            "preference",
            {"key": "favorite_film", "value": "Inception", "subcategory": "entertainment"},
        )
        assert result.success is True
        assert result.updated_profile["preferences"]["entertainment"]["favorite_film"] == "Inception"
        assert result.updated_profile["preferences"]["entertainment"]["favorite_fantasy_series"] == "Star Trek"

    def test_save_updates_existing_key(self):
        profile = {"preferences": {"entertainment": {"favorite_fantasy_series": "Star Trek"}}}
        result = _apply_memory_update(
            profile,
            "save",
            "preference",
            {"key": "favorite_fantasy_series", "value": "Star Wars", "subcategory": "entertainment"},
        )
        assert result.success is True
        assert result.updated_profile["preferences"]["entertainment"]["favorite_fantasy_series"] == "Star Wars"

    def test_save_creates_preferences_section(self):
        profile = {}
        result = _apply_memory_update(
            profile, "save", "preference", {"key": "editor", "value": "Neovim", "subcategory": "tech"}
        )
        assert result.success is True
        assert result.updated_profile["preferences"]["tech"]["editor"] == "Neovim"

    def test_save_missing_key_returns_invalid(self):
        result = _apply_memory_update({}, "save", "preference", {"key": "", "value": "test"})
        assert result.success is False
        assert result.allow_fallback is True

    def test_save_missing_value_returns_invalid(self):
        result = _apply_memory_update({}, "save", "preference", {"key": "sport", "value": ""})
        assert result.success is False
        assert result.allow_fallback is True


# ---------------------------------------------------------------------------
# _apply_memory_update – delete preference (Kernfix #40)
# Phase 121: result.updated_profile statt result[...]
# ---------------------------------------------------------------------------


class TestApplyMemoryUpdatePreferenceDelete:
    def test_delete_by_exact_key_flat(self, profile_flat):
        result = _apply_memory_update(profile_flat, "delete", "preference", {"key": "favorite_fantasy_series"})
        assert result.success is True
        assert "favorite_fantasy_series" not in result.updated_profile["preferences"]

    def test_delete_by_exact_key_nested(self, profile_nested):
        result = _apply_memory_update(profile_nested, "delete", "preference", {"key": "favorite_fantasy_series"})
        assert result.success is True
        assert "favorite_fantasy_series" not in result.updated_profile["preferences"].get("entertainment", {})

    def test_delete_by_value_flat(self, profile_flat):
        result = _apply_memory_update(profile_flat, "delete", "preference", {"key": "star trek"})
        assert result.success is True
        assert "favorite_fantasy_series" not in result.updated_profile["preferences"]

    def test_delete_by_value_nested(self, profile_nested):
        result = _apply_memory_update(profile_nested, "delete", "preference", {"key": "star trek"})
        assert result.success is True
        assert "favorite_fantasy_series" not in result.updated_profile["preferences"].get("entertainment", {})

    def test_delete_cleans_empty_subcategory(self):
        profile = {"preferences": {"entertainment": {"favorite_fantasy_series": "Star Trek"}}}
        result = _apply_memory_update(profile, "delete", "preference", {"key": "favorite_fantasy_series"})
        assert result.success is True
        assert "entertainment" not in result.updated_profile["preferences"]

    def test_delete_keeps_other_keys_in_subcategory(self, profile_nested):
        result = _apply_memory_update(profile_nested, "delete", "preference", {"key": "favorite_fantasy_series"})
        assert result.success is True
        assert result.updated_profile["preferences"]["entertainment"]["favorite_film"] == "Inception"

    def test_delete_no_match_returns_reject(self, profile_flat):
        result = _apply_memory_update(profile_flat, "delete", "preference", {"key": "nonexistent_key_xyz"})
        assert result.success is False
        assert "nonexistent_key_xyz" in result.user_message

    def test_delete_empty_key_returns_invalid(self, profile_flat):
        result = _apply_memory_update(profile_flat, "delete", "preference", {"key": ""})
        assert result.success is False
        assert result.allow_fallback is True

    def test_delete_case_insensitive(self, profile_flat):
        result = _apply_memory_update(profile_flat, "delete", "preference", {"key": "SPORT"})
        assert result.success is True
        assert "sport" not in result.updated_profile["preferences"]


# ---------------------------------------------------------------------------
# _apply_memory_update – delete project early return fix (#43)
# Phase 121: leerer name → _reject (allow_fallback=False)
# ---------------------------------------------------------------------------


class TestDeleteProjectEmptyName:
    def test_delete_project_empty_name_returns_reject(self):
        profile = {"projects": {"active": [{"name": "FabBot", "priority": "high"}]}}
        result = _apply_memory_update(profile, "delete", "project", {"name": ""})
        assert result.success is False
        assert result.allow_fallback is False
        assert result.user_message is not None


# ---------------------------------------------------------------------------
# _build_clarify_message
# ---------------------------------------------------------------------------


class TestBuildClarifyMessage:
    def test_contains_question(self):
        data = {
            "question": "Meinst du favorite_fantasy_series oder media Star Trek?",
            "options": ["preferences.entertainment.favorite_fantasy_series", "media.Star Trek"],
        }
        msg = _build_clarify_message(data)
        assert "Meinst du" in msg

    def test_contains_options(self):
        data = {
            "question": "Welchen Eintrag meinst du?",
            "options": ["preferences.entertainment.favorite_fantasy_series", "media.Star Trek"],
        }
        msg = _build_clarify_message(data)
        assert "preferences.entertainment.favorite_fantasy_series" in msg
        assert "media.Star Trek" in msg

    def test_max_5_options(self):
        data = {"question": "Welchen?", "options": [f"option_{i}" for i in range(10)]}
        msg = _build_clarify_message(data)
        assert msg.count("•") <= 5

    def test_fallback_without_options(self):
        data = {"question": "Was meinst du?"}
        msg = _build_clarify_message(data)
        assert "Was meinst du?" in msg

    def test_fallback_without_question(self):
        data = {}
        msg = _build_clarify_message(data)
        assert "?" in msg


# ---------------------------------------------------------------------------
# memory_agent() integration – clarify flow
# ---------------------------------------------------------------------------


class TestMemoryAgentClarifyFlow:
    @pytest.mark.asyncio
    async def test_clarify_action_returns_question(self):
        messages = [HumanMessage(content="Lösche Star Trek")]

        clarify_response = {
            "action": "clarify",
            "category": "preference",
            "data": {
                "question": "Meinst du favorite_fantasy_series (Star Trek) oder etwas anderes?",
                "options": ["preferences.entertainment.favorite_fantasy_series"],
            },
        }

        with (
            patch("agent.agents.memory_agent.load_profile", return_value={}),
            patch(
                "agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock, return_value=clarify_response
            ),
            patch("agent.agents.memory_agent.write_profile") as mock_write,
        ):
            from agent.agents.memory_agent import memory_agent

            result = await memory_agent({"messages": messages})

            mock_write.assert_not_called()
            msg = result["messages"][-1].content
            assert "Meinst du" in msg or "?" in msg

    @pytest.mark.asyncio
    async def test_save_preference_nested_written_to_profile(self):
        messages = [HumanMessage(content="Merke dir: Meine Lieblings-Serie ist The Wire")]

        save_response = {
            "action": "save",
            "category": "preference",
            "data": {"key": "favorite_series", "value": "The Wire", "subcategory": "entertainment"},
        }

        captured = {}

        async def mock_write(profile):
            captured["profile"] = profile
            return True

        with (
            patch("agent.agents.memory_agent.load_profile", return_value={"preferences": {}}),
            patch("agent.agents.memory_agent._parse_memory_intent", new_callable=AsyncMock, return_value=save_response),
            patch("agent.agents.memory_agent._review_yaml", new_callable=AsyncMock, return_value=True),
            patch("agent.agents.memory_agent.write_profile", side_effect=mock_write),
        ):
            from agent.agents.memory_agent import memory_agent

            await memory_agent({"messages": messages})

            assert "preferences" in captured["profile"]
            assert captured["profile"]["preferences"]["entertainment"]["favorite_series"] == "The Wire"
