"""
Tests Phase 115 – _review_yaml generisch delete-aware (Issue #39).

Testet _is_valid_delete für alle Kategorien:
people, places, media, preference, job, location, custom, project.

Importiert _is_valid_delete direkt aus agent.agents.memory_agent –
kein sys.modules-Patching, keine Seiteneffekte auf andere Tests.
"""

import copy
import pytest
import yaml
from agent.agents.memory_agent import _is_valid_delete


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def full_profile():
    return {
        "identity": {"name": "Fabio", "location": "Berlin, Germany"},
        "work": {"employer": "Acme GmbH", "role": "Lead Engineer", "job_context": "Remote"},
        "people": [
            {"name": "Alice", "context": "Kollegin"},
            {"name": "Bob", "context": "Freund"},
        ],
        "places": [
            {"name": "Sissi", "type": "restaurant", "location": "Prenzlauer Berg"},
            {"name": "Berghain", "type": "bar", "location": "Friedrichshain"},
        ],
        "media": [
            {"title": "Aphex Twin - Selected Ambient Works", "type": "album", "artist": "Aphex Twin"},
            {"title": "Dune", "type": "film"},
        ],
        "preferences": {"sprache": "Deutsch", "antwort_stil": "kurz und direkt"},
        "custom": [
            {"key": "lieblingsfarbe", "value": "blau"},
            {"key": "sport", "value": "Bouldern"},
        ],
        "projects": {
            "active": [
                {"name": "FabBot", "description": "Telegram AI Bot", "priority": "high"},
                {"name": "AbletonAI", "description": "Music AI", "priority": "medium"},
            ]
        },
    }


# ---------------------------------------------------------------------------
# Tests – valide Deletes (alle Kategorien)
# ---------------------------------------------------------------------------

class TestIsValidDeleteValid:

    def test_delete_people(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["people"] = [p for p in updated["people"] if p["name"] != "Alice"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_place(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["places"] = [p for p in updated["places"] if p["name"] != "Sissi"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_media(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["media"] = [m for m in updated["media"] if m["title"] != "Dune"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_preference(self, full_profile):
        updated = copy.deepcopy(full_profile)
        del updated["preferences"]["sprache"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_location(self, full_profile):
        updated = copy.deepcopy(full_profile)
        del updated["identity"]["location"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_job_field(self, full_profile):
        updated = copy.deepcopy(full_profile)
        del updated["work"]["job_context"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_job_entire_block(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["work"] = {}
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_custom(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["custom"] = [i for i in updated["custom"] if i["key"] != "sport"]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_project(self, full_profile):
        updated = copy.deepcopy(full_profile)
        updated["projects"]["active"] = [
            p for p in updated["projects"]["active"] if p["name"] != "AbletonAI"
        ]
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_all_people(self, full_profile):
        """Alle Personen gelöscht → leere Liste ist valider Subset."""
        updated = copy.deepcopy(full_profile)
        updated["people"] = []
        assert _is_valid_delete(full_profile, updated) is True

    def test_delete_nested_value(self):
        """Tief verschachtelter Delete ist valider Subset."""
        original = {"a": {"b": {"c": 1, "d": 2}}}
        updated = {"a": {"b": {"c": 1}}}
        assert _is_valid_delete(original, updated) is True


# ---------------------------------------------------------------------------
# Tests – invalide Deletes
# ---------------------------------------------------------------------------

class TestIsValidDeleteInvalid:

    def test_no_change(self, full_profile):
        """Identisches YAML → kein Delete passiert → INVALID."""
        updated = copy.deepcopy(full_profile)
        assert _is_valid_delete(full_profile, updated) is False

    def test_new_person_added(self, full_profile):
        """Neuer Eintrag hinzugefügt → kein valider Delete."""
        updated = copy.deepcopy(full_profile)
        updated["people"].append({"name": "Eve", "context": "Neu"})
        assert _is_valid_delete(full_profile, updated) is False

    def test_value_changed(self, full_profile):
        """Wert geändert statt gelöscht → INVALID."""
        updated = copy.deepcopy(full_profile)
        updated["identity"]["location"] = "München"
        assert _is_valid_delete(full_profile, updated) is False

    def test_new_key_in_dict(self, full_profile):
        """Neuer Key in Dict hinzugefügt → INVALID."""
        updated = copy.deepcopy(full_profile)
        updated["preferences"]["neu"] = "wert"
        assert _is_valid_delete(full_profile, updated) is False

    def test_empty_original_no_change(self):
        """Leeres Original, leeres Updated → keine Änderung → INVALID."""
        assert _is_valid_delete({}, {}) is False

    def test_nested_value_changed(self):
        """Tief verschachtelter Wert geändert → INVALID."""
        original = {"a": {"b": {"c": 1}}}
        updated = {"a": {"b": {"c": 99}}}
        assert _is_valid_delete(original, updated) is False

    def test_new_top_level_key(self, full_profile):
        """Neuer Top-Level-Key → INVALID."""
        updated = copy.deepcopy(full_profile)
        updated["extra"] = {"key": "wert"}
        assert _is_valid_delete(full_profile, updated) is False


# ---------------------------------------------------------------------------
# Tests – Edge Cases
# ---------------------------------------------------------------------------

class TestIsValidDeleteEdgeCases:

    def test_empty_updated_is_valid_subset(self):
        """Leeres updated ist Subset von jedem original (alle Daten gelöscht)."""
        original = {"people": [{"name": "Alice"}], "preferences": {"key": "val"}}
        assert _is_valid_delete(original, {}) is True

    def test_single_item_list_delete(self):
        """Letztes Element aus Liste gelöscht → valider Delete."""
        original = {"places": [{"name": "Sissi"}]}
        updated = {"places": []}
        assert _is_valid_delete(original, updated) is True

    def test_both_empty_invalid(self):
        """Beide leer → keine Änderung → INVALID."""
        assert _is_valid_delete({}, {}) is False
