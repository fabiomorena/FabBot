"""
tests/test_ph238_pref_delete_pfad.py – Phase 238 (Issue #352)

Der Parser sieht Preferences als Pfad (`preferences.lifestyle.ernaehrung = "vegan"`)
und liefert beim Löschen diesen Pfad als Key. Testet, dass _apply_memory_update
Pfad-Keys auflöst und die bisherigen Key-/Wert-Matches unverändert bleiben.
"""

import pytest

from agent.agents.memory_agent import _apply_memory_update


@pytest.fixture
def profile():
    return {
        "preferences": {
            "lifestyle": {"ernaehrung": "vegan", "sport": "Laufen"},
            "tech": {"editor": "Zed"},
            "lieblingsfarbe": "blau",
        }
    }


def _delete(profile: dict, key: str):
    return _apply_memory_update(profile, "delete", "preference", {"key": key})


class TestPfadKey:
    def test_full_path(self, profile):
        result = _delete(profile, "preferences.lifestyle.ernaehrung")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]
        assert result.updated_profile["preferences"]["lifestyle"]["sport"] == "Laufen"

    def test_path_without_prefix(self, profile):
        result = _delete(profile, "lifestyle.ernaehrung")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]

    def test_full_path_case_insensitive(self, profile):
        result = _delete(profile, "Preferences.Lifestyle.Ernaehrung")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]

    def test_full_path_removes_empty_subcategory(self, profile):
        result = _delete(profile, "preferences.tech.editor")
        assert result.success is True
        assert "tech" not in result.updated_profile["preferences"]

    def test_flat_path(self, profile):
        result = _delete(profile, "preferences.lieblingsfarbe")
        assert result.success is True
        assert "lieblingsfarbe" not in result.updated_profile["preferences"]

    def test_path_with_wrong_subcategory_falls_back_to_key(self, profile):
        """Subcategory falsch geraten, Key existiert → letztes Segment greift."""
        result = _delete(profile, "preferences.entertainment.ernaehrung")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]

    def test_unknown_path_rejected(self, profile):
        result = _delete(profile, "preferences.lifestyle.gibtsnicht")
        assert result.success is False
        assert result.allow_fallback is False


class TestBisherigesVerhaltenUnveraendert:
    def test_bare_key(self, profile):
        result = _delete(profile, "ernaehrung")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]

    def test_value(self, profile):
        result = _delete(profile, "vegan")
        assert result.success is True
        assert "ernaehrung" not in result.updated_profile["preferences"]["lifestyle"]
