"""
tests/test_ph226_profile_pydantic.py

Phase 226 – Issue #198: Weiche Pydantic-Validierung für personal_profile.yaml.

Testet:
- Valides Vollprofil → load_profile() liefert alle Sektionen unverändert
- Unbekanntes Top-Level-Feld → kein Fehler, Feld bleibt erhalten, DEBUG-Log
- Typfehler (identity.name: 123) → WARNING-Log + Fallback auf vollständiges Roh-dict
- Leeres/fehlendes Profil → weiterhin {}
- Hash-Stabilität: model_dump(exclude_unset=True) fügt keine Felder hinzu/weg
"""

import logging
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile_module(tmp_path: Path):
    import importlib
    import agent.profile as mod

    importlib.reload(mod)
    mod._PROFILE_PATH = tmp_path / "personal_profile.yaml"
    mod._BACKUP_PATH = tmp_path / "personal_profile.yaml.bak"
    mod._profile_cache = None
    mod._migration_done = False
    mod._profile_snapshot = None
    mod._snapshot_expires_at = 0.0
    return mod


def _load_plain(mod, yaml_text: str):
    """Schreibt Klartext-YAML und lädt via load_profile() (Migration gemockt)."""
    mod._PROFILE_PATH.write_bytes(yaml_text.encode())
    with (
        patch("agent.crypto.is_encrypted", return_value=False),
        patch("agent.crypto.encrypt", return_value=b"enc"),
    ):
        return mod.load_profile()


_FULL_PROFILE = """\
identity:
  name: Fabio
  location: Berlin
  language: de
work:
  employer: Selbststaendig
  role: Entwickler
projects:
  active:
    - name: FabBot
      description: Persoenlicher Assistent
      stack:
        - Python
        - LangGraph
      priority: high
people:
  - name: Marco
    context: Freund
places:
  - name: Berghain
    type: club
    location: Berlin
preferences:
  communication: kurz
  dislikes:
    - Smalltalk
hardware:
  main_machine: MacBook Air M3
routines:
  deep_work: vormittags
notes:
  - "[01.01.2026 10:00] Testnote"
custom:
  - key: lieblingsfarbe
    value: blau
"""


# ---------------------------------------------------------------------------
# Valides Vollprofil
# ---------------------------------------------------------------------------


class TestValidFullProfile:
    def test_all_sections_preserved(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p = _load_plain(mod, _FULL_PROFILE)

        assert p["identity"]["name"] == "Fabio"
        assert p["identity"]["location"] == "Berlin"
        assert p["work"]["role"] == "Entwickler"
        assert p["projects"]["active"][0]["name"] == "FabBot"
        assert p["projects"]["active"][0]["stack"] == ["Python", "LangGraph"]
        assert p["people"][0]["name"] == "Marco"
        assert p["places"][0]["type"] == "club"
        assert p["preferences"]["dislikes"] == ["Smalltalk"]
        assert p["hardware"]["main_machine"] == "MacBook Air M3"
        assert p["routines"]["deep_work"] == "vormittags"
        assert p["notes"] == ["[01.01.2026 10:00] Testnote"]
        assert p["custom"][0]["key"] == "lieblingsfarbe"

    def test_no_extra_none_keys_added(self, tmp_path):
        """exclude_unset → keine None-Defaults für nicht gesetzte Felder."""
        mod = _make_profile_module(tmp_path)
        p = _load_plain(mod, "identity:\n  name: Fabio\n")

        assert p == {"identity": {"name": "Fabio"}}
        assert "work" not in p
        assert "language" not in p["identity"]


# ---------------------------------------------------------------------------
# Unbekannte Felder (extra="allow")
# ---------------------------------------------------------------------------


class TestUnknownFields:
    def test_unknown_top_level_field_preserved(self, tmp_path, caplog):
        mod = _make_profile_module(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="agent.profile"):
            p = _load_plain(mod, "identity:\n  name: Fabio\nfoobar:\n  beliebig: wert\n")

        assert p["foobar"] == {"beliebig": "wert"}, "unbekannte Sektion darf nicht verloren gehen"
        assert any("unbekannte Felder" in r.message for r in caplog.records)

    def test_unknown_nested_field_preserved(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p = _load_plain(mod, "identity:\n  name: Fabio\n  zodiac: Loewe\n")

        assert p["identity"]["zodiac"] == "Loewe"


# ---------------------------------------------------------------------------
# people: dict ODER list (reale Struktur ist ein einzelnes dict)
# ---------------------------------------------------------------------------


class TestPeopleShape:
    def test_people_as_single_dict_no_warning(self, tmp_path, caplog):
        """Reales Profil hat people als einzelnes dict – muss valide sein, keine WARNING."""
        mod = _make_profile_module(tmp_path)
        with caplog.at_level(logging.WARNING, logger="agent.profile"):
            p = _load_plain(mod, "people:\n  name: Stephanie\n  context: Partnerin\n")

        assert p["people"] == {"name": "Stephanie", "context": "Partnerin"}
        assert not any("Fallback auf Rohwert" in r.message for r in caplog.records)

    def test_people_as_list_still_valid(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p = _load_plain(mod, "people:\n  - name: Marco\n    context: Freund\n")
        assert p["people"] == [{"name": "Marco", "context": "Freund"}]


# ---------------------------------------------------------------------------
# Typfehler → Fallback auf Rohwert
# ---------------------------------------------------------------------------


class TestTypeErrorFallback:
    def test_type_error_falls_back_to_raw(self, tmp_path, caplog):
        mod = _make_profile_module(tmp_path)
        # name: 123 (int statt str) → ValidationError → Fallback
        yaml_text = "identity:\n  name: 123\nwork:\n  role: Entwickler\n"
        with caplog.at_level(logging.WARNING, logger="agent.profile"):
            p = _load_plain(mod, yaml_text)

        # Vollständiges Roh-dict, kein Crash, kein Datenverlust
        assert p == {"identity": {"name": 123}, "work": {"role": "Entwickler"}}
        assert any("Fallback auf Rohwert" in r.message for r in caplog.records)

    def test_type_error_no_crash(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        # stack soll list sein, hier int → Fallback statt Exception
        p = _load_plain(mod, "projects:\n  active:\n    - name: X\n      stack: 5\n")
        assert p["projects"]["active"][0]["stack"] == 5


# ---------------------------------------------------------------------------
# Leeres / fehlendes Profil
# ---------------------------------------------------------------------------


class TestEmptyProfile:
    def test_missing_file_returns_empty(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        # Datei existiert nicht
        assert mod.load_profile() == {}

    def test_empty_yaml_returns_empty(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p = _load_plain(mod, "")
        assert p == {}


# ---------------------------------------------------------------------------
# Hash-Stabilität (schützt write_profile Optimistic-Concurrency)
# ---------------------------------------------------------------------------


class TestHashStability:
    def test_validated_dump_hash_equals_raw_hash(self, tmp_path):
        """
        _validate_profile darf den Profil-Inhalt nicht verändern:
        Hash des validierten Dumps == Hash des Roh-Parses.
        """
        import yaml

        mod = _make_profile_module(tmp_path)
        raw = yaml.safe_load(_FULL_PROFILE)
        validated = _load_plain(mod, _FULL_PROFILE)

        assert mod._compute_profile_hash(validated) == mod._compute_profile_hash(raw)
