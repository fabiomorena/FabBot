"""
tests/test_ph201_event_memory.py – Phase 201 (Issues #202 + #205)

#202: Memory-Agent speichert Events korrekt unter events.*
#205: Curator erkennt falsch kategorisierte Preferences und archiviert sie
"""

import pytest
from agent.agents.memory_agent import _apply_memory_update, _build_confirmation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_profile():
    return {}


@pytest.fixture
def profile_with_misclassified_pref():
    return {
        "preferences": {
            "persoenlich": {
                "reise_kassel_ticket_gekauft": "true",
                "lieblingsfarbe": "blau",
            }
        }
    }


# ---------------------------------------------------------------------------
# #202: Event save
# ---------------------------------------------------------------------------


class TestEventSave:
    def test_save_creates_events_list(self, empty_profile):
        r = _apply_memory_update(
            empty_profile,
            "save",
            "event",
            {"description": "Zugticket nach Kassel gekauft", "date": "2026-05-11", "tags": ["reise", "kassel"]},
        )
        assert r.success is True
        events = r.updated_profile.get("events", [])
        assert len(events) == 1
        assert events[0]["description"] == "Zugticket nach Kassel gekauft"
        assert events[0]["date"] == "2026-05-11"
        assert "kassel" in events[0]["tags"]

    def test_save_appends_to_existing_events(self):
        profile = {"events": [{"description": "Erstes Ereignis"}]}
        r = _apply_memory_update(profile, "save", "event", {"description": "Zweites Ereignis"})
        assert r.success is True
        assert len(r.updated_profile["events"]) == 2

    def test_save_without_date(self, empty_profile):
        r = _apply_memory_update(empty_profile, "save", "event", {"description": "Projekt abgeschlossen"})
        assert r.success is True
        event = r.updated_profile["events"][0]
        assert "date" not in event

    def test_save_without_tags(self, empty_profile):
        r = _apply_memory_update(empty_profile, "save", "event", {"description": "Kurzer Event"})
        assert r.success is True
        event = r.updated_profile["events"][0]
        assert "tags" not in event

    def test_save_empty_description_returns_invalid(self, empty_profile):
        r = _apply_memory_update(empty_profile, "save", "event", {"description": "", "tags": []})
        assert r.success is False
        assert r.allow_fallback is True

    def test_save_does_not_mutate_original(self, empty_profile):
        import copy

        orig = copy.deepcopy(empty_profile)
        _apply_memory_update(empty_profile, "save", "event", {"description": "Test"})
        assert empty_profile == orig


class TestEventDelete:
    def test_delete_by_partial_description(self):
        profile = {"events": [{"description": "Zugticket nach Kassel gekauft"}, {"description": "Zahnarzttermin"}]}
        r = _apply_memory_update(profile, "delete", "event", {"description": "kassel"})
        assert r.success is True
        descriptions = [e["description"] for e in r.updated_profile["events"]]
        assert "Zugticket nach Kassel gekauft" not in descriptions
        assert "Zahnarzttermin" in descriptions

    def test_delete_no_match_returns_reject(self):
        profile = {"events": [{"description": "Zahnarzttermin"}]}
        r = _apply_memory_update(profile, "delete", "event", {"description": "kassel"})
        assert r.success is False
        assert r.allow_fallback is False

    def test_delete_empty_description_returns_invalid(self):
        profile = {"events": [{"description": "Test"}]}
        r = _apply_memory_update(profile, "delete", "event", {"description": ""})
        assert r.success is False
        assert r.allow_fallback is True


class TestEventConfirmation:
    def test_confirmation_with_date(self):
        msg = _build_confirmation("save", "event", {"description": "Ticket gekauft", "date": "2026-05-11"})
        assert "Ticket gekauft" in msg
        assert "2026-05-11" in msg

    def test_confirmation_without_date(self):
        msg = _build_confirmation("save", "event", {"description": "Projekt abgeschlossen"})
        assert "Projekt abgeschlossen" in msg
        assert "(" not in msg


# ---------------------------------------------------------------------------
# #205: Curator – misclassified preferences
# ---------------------------------------------------------------------------


class TestCuratorMisclassifiedPreferences:
    def test_sanitize_valid_path(self):
        from agent.proactive.curator import _sanitize_analysis

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [
                {"path": "preferences.persoenlich.reise_kassel_ticket_gekauft", "reason": "Einmalige Handlung"}
            ],
            "summary": "",
        }
        result = _sanitize_analysis(analysis)
        assert len(result["misclassified_preferences"]) == 1

    def test_sanitize_rejects_non_preference_path(self):
        from agent.proactive.curator import _sanitize_analysis

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [
                {"path": "people.0.name", "reason": "test"},
                {"path": "preferences.", "reason": "zu kurz"},
                {"path": "preferences.persoenlich", "reason": "kein key"},
            ],
            "summary": "",
        }
        result = _sanitize_analysis(analysis)
        assert result["misclassified_preferences"] == []

    def test_sanitize_rejects_non_string_path(self):
        from agent.proactive.curator import _sanitize_analysis

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [{"path": 42, "reason": "test"}],
            "summary": "",
        }
        result = _sanitize_analysis(analysis)
        assert result["misclassified_preferences"] == []

    def test_build_proposal_archives_misclassified(self, profile_with_misclassified_pref):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [
                {
                    "path": "preferences.persoenlich.reise_kassel_ticket_gekauft",
                    "reason": "Einmalige Handlung statt dauerhafter Zustand",
                }
            ],
            "summary": "Falsch kategorierter Event-Eintrag gefunden.",
        }
        proposal = _build_proposal(profile_with_misclassified_pref, analysis)

        ops = proposal["operations"]
        assert len(ops) == 1
        assert ops[0]["type"] == "archive_preference"
        assert ops[0]["path"] == "preferences.persoenlich.reise_kassel_ticket_gekauft"

        target = proposal["target_profile"]
        # Eintrag aus preferences entfernt
        assert "reise_kassel_ticket_gekauft" not in target["preferences"]["persoenlich"]
        # Andere Präferenz bleibt
        assert target["preferences"]["persoenlich"].get("lieblingsfarbe") == "blau"
        # Im archived-Block
        archived = target.get("archived", [])
        assert any(a.get("_key") == "preferences.persoenlich.reise_kassel_ticket_gekauft" for a in archived)

    def test_build_proposal_removes_empty_subcategory(self):
        from agent.proactive.curator import _build_proposal

        profile = {"preferences": {"persoenlich": {"ticket": "true"}}}
        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [{"path": "preferences.persoenlich.ticket", "reason": "Einmalig"}],
            "summary": "",
        }
        proposal = _build_proposal(profile, analysis)
        target = proposal["target_profile"]
        # Subcategory komplett entfernt wenn leer
        assert "persoenlich" not in target.get("preferences", {})

    def test_build_proposal_unknown_path_skipped(self, profile_with_misclassified_pref):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [],
            "misclassified_preferences": [{"path": "preferences.persoenlich.nicht_vorhanden", "reason": "test"}],
            "summary": "",
        }
        proposal = _build_proposal(profile_with_misclassified_pref, analysis)
        # Kein Fehler, keine Operation
        assert proposal["operations"] == []

    def test_format_report_shows_misclassified(self):
        from agent.proactive.curator import format_report
        from datetime import datetime, timezone, timedelta

        proposal = {
            "operations": [
                {
                    "type": "archive_preference",
                    "path": "preferences.persoenlich.reise_kassel_ticket_gekauft",
                    "reason": "Einmalige Handlung",
                }
            ],
            "summary": "Event-Eintrag unter preferences gefunden.",
            "created_at": "2026-05-11T10:00:00+00:00",
        }
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat()
        report = format_report(proposal, expires_at)

        assert "Falsch kategorisierte Preferences" in report
        assert "preferences.persoenlich.reise_kassel_ticket_gekauft" in report
        assert "Einmalige Handlung" in report
