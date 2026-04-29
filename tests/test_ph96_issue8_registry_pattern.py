"""
Tests fuer Phase 96 – Issue #8: _apply_memory_update Registry-Pattern.

Prueft:
- Alle 8 Save-Handler (people, project, place, media, preference, job, location, custom)
- Alle 5 Delete-Handler (people, project, place, media, custom)
- Unbekannte category → None
- Unbekannte action → None
- Verhaltensaequivalenz zum alten Switch-Code (Regression)
"""

import copy
import pytest

from agent.memory_agent import _apply_memory_update


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_profile():
    return {}


@pytest.fixture
def full_profile():
    return {
        "identity": {"location": "Berlin, Germany"},
        "people": [{"name": "Steffi", "context": "Freundin"}],
        "projects": {
            "active": [{"name": "FabBot", "description": "AI Assistant", "stack": ["Python"], "priority": "high"}]
        },
        "places": [{"name": "Berghain", "type": "bar", "location": "Berlin", "context": "Lieblingsclub"}],
        "media": [{"title": "Autechre", "type": "künstler", "context": "Favorit"}],
        "preferences": {"sprache": "Deutsch"},
        "work": {"employer": "Freelance", "role": "Developer"},
        "custom": [{"key": "motto", "value": "Keep it simple"}],
    }


# ---------------------------------------------------------------------------
# Save-Handler Tests
# ---------------------------------------------------------------------------


class TestSavePeople:
    def test_neue_person(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "people", {"name": "Anna", "context": "Kollegin"})
        assert result is not None
        assert result["people"] == [{"name": "Anna", "context": "Kollegin"}]

    def test_existing_person_update_context(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "people", {"name": "Steffi", "context": "Verlobte"})
        assert result is not None
        steffi = next(p for p in result["people"] if p["name"] == "Steffi")
        assert steffi["context"] == "Verlobte"

    def test_missing_name_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "people", {"context": "jemand"}) is None

    def test_case_insensitive_match(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "people", {"name": "STEFFI", "context": "Beste Freundin"})
        assert result is not None
        steffi = next(p for p in result["people"] if p["name"] == "Steffi")
        assert steffi["context"] == "Beste Freundin"


class TestSaveProject:
    def test_neues_projekt(self, empty_profile):
        result = _apply_memory_update(
            empty_profile,
            "save",
            "project",
            {"name": "TestApp", "description": "Test", "stack": ["Python"], "priority": "low"},
        )
        assert result is not None
        assert result["projects"]["active"][0]["name"] == "TestApp"

    def test_existing_project_update(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "project", {"name": "FabBot", "description": "Updated"})
        assert result is not None
        fb = next(p for p in result["projects"]["active"] if p["name"] == "FabBot")
        assert fb["description"] == "Updated"

    def test_missing_name_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "project", {"description": "no name"}) is None

    def test_default_priority_medium(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "project", {"name": "NoPrio"})
        assert result["projects"]["active"][0]["priority"] == "medium"


class TestSavePlace:
    def test_neuer_ort(self, empty_profile):
        result = _apply_memory_update(
            empty_profile, "save", "place", {"name": "Tresor", "type": "bar", "location": "Berlin"}
        )
        assert result is not None
        assert result["places"][0]["name"] == "Tresor"

    def test_existing_place_update(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "place", {"name": "Berghain", "context": "Techno-Tempel"})
        assert result is not None
        bg = next(p for p in result["places"] if p["name"] == "Berghain")
        assert bg["context"] == "Techno-Tempel"

    def test_missing_name_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "place", {"type": "bar"}) is None


class TestSaveMedia:
    def test_neues_media(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "media", {"title": "Blade Runner", "type": "film"})
        assert result is not None
        assert result["media"][0]["title"] == "Blade Runner"

    def test_existing_media_update(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "media", {"title": "Autechre", "context": "Genial"})
        assert result is not None
        ae = next(m for m in result["media"] if m["title"] == "Autechre")
        assert ae["context"] == "Genial"

    def test_missing_title_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "media", {"type": "film"}) is None


class TestSavePreference:
    def test_neue_preference(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "preference", {"key": "theme", "value": "dark"})
        assert result is not None
        assert result["preferences"]["theme"] == "dark"

    def test_update_existing(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "preference", {"key": "sprache", "value": "English"})
        assert result["preferences"]["sprache"] == "English"

    def test_missing_key_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "preference", {"value": "dark"}) is None

    def test_missing_value_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "preference", {"key": "theme"}) is None


class TestSaveJob:
    def test_neuer_job(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "job", {"employer": "Acme", "role": "Dev"})
        assert result is not None
        assert result["work"]["employer"] == "Acme"
        assert result["work"]["role"] == "Dev"

    def test_update_job(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "job", {"employer": "BigCorp", "role": "CTO"})
        assert result["work"]["employer"] == "BigCorp"
        assert result["work"]["role"] == "CTO"

    def test_missing_employer_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "job", {"role": "Dev"}) is None

    def test_job_context_saved(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "job", {"employer": "XYZ", "context": "Remote"})
        assert result["work"]["job_context"] == "Remote"


class TestSaveLocation:
    def test_neue_location(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "location", {"location": "Hamburg, Germany"})
        assert result is not None
        assert result["identity"]["location"] == "Hamburg, Germany"

    def test_update_location(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "location", {"location": "München, Germany"})
        assert result["identity"]["location"] == "München, Germany"

    def test_missing_location_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "location", {}) is None


class TestSaveCustom:
    def test_neuer_custom(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "custom", {"key": "hobby", "value": "Musik"})
        assert result is not None
        assert result["custom"][0] == {"key": "hobby", "value": "Musik"}

    def test_update_existing(self, full_profile):
        result = _apply_memory_update(full_profile, "update", "custom", {"key": "motto", "value": "Ship it"})
        item = next(i for i in result["custom"] if i["key"] == "motto")
        assert item["value"] == "Ship it"

    def test_missing_key_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "custom", {"value": "x"}) is None

    def test_missing_value_returns_none(self, empty_profile):
        assert _apply_memory_update(empty_profile, "save", "custom", {"key": "x"}) is None


# ---------------------------------------------------------------------------
# Delete-Handler Tests
# ---------------------------------------------------------------------------


class TestDeleteHandlers:
    def test_delete_people(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "people", {"name": "Steffi"})
        assert result is not None
        assert not any(p.get("name") == "Steffi" for p in result.get("people", []))

    def test_delete_project(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "project", {"name": "FabBot"})
        assert result is not None
        assert not any(p.get("name") == "FabBot" for p in result["projects"]["active"])

    def test_delete_place(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "place", {"name": "Berghain"})
        assert result is not None
        assert not any(p.get("name") == "Berghain" for p in result.get("places", []))

    def test_delete_media(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "media", {"title": "Autechre"})
        assert result is not None
        assert not any(m.get("title") == "Autechre" for m in result.get("media", []))

    def test_delete_custom(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "custom", {"key": "motto"})
        assert result is not None
        assert not any(i.get("key") == "motto" for i in result.get("custom", []))

    def test_delete_nonexistent_no_crash(self, full_profile):
        result = _apply_memory_update(full_profile, "delete", "people", {"name": "Niemand"})
        assert result is not None  # kein Crash, kein None


# ---------------------------------------------------------------------------
# Registry Fallback Tests
# ---------------------------------------------------------------------------


class TestRegistryFallback:
    def test_unknown_category_save_returns_none(self, empty_profile):
        result = _apply_memory_update(empty_profile, "save", "unknown_category", {"key": "x"})
        assert result is None

    def test_unknown_category_delete_returns_none(self, empty_profile):
        result = _apply_memory_update(empty_profile, "delete", "unknown_category", {"name": "x"})
        assert result is None

    def test_unknown_action_returns_none(self, empty_profile):
        result = _apply_memory_update(empty_profile, "upsert", "people", {"name": "x"})
        assert result is None

    def test_bot_instruction_save_returns_none(self, empty_profile):
        """bot_instruction wird in memory_agent() abgefangen – darf nie hier ankommen."""
        result = _apply_memory_update(empty_profile, "save", "bot_instruction", {"text": "test"})
        assert result is None


# ---------------------------------------------------------------------------
# Immutability Test
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_original_not_mutated(self, full_profile):
        original = copy.deepcopy(full_profile)
        _apply_memory_update(full_profile, "save", "people", {"name": "Neu", "context": "Test"})
        assert full_profile == original
