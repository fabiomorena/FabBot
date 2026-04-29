"""
tests/test_ph137_supervisor_routing.py – Phase 137 (Issue #55)

Testet das deterministische Pre-Routing des Supervisors.
Stellt sicher dass alle Trigger-Gruppen korrekt routen,
die Reihenfolge (spezifischer vor generischem) eingehalten wird
und unbekannte Eingaben None zurückgeben.
"""

from agent.supervisor import _match_pre_routing


class TestOpinionTrigger:
    def test_was_haelst_du(self):
        agent, label = _match_pre_routing("Was hälst du von Python?")
        assert agent == "chat_agent"
        assert "opinion" in label

    def test_was_denkst_du(self):
        agent, _ = _match_pre_routing("Was denkst du darüber?")
        assert agent == "chat_agent"

    def test_wie_findest_du(self):
        agent, _ = _match_pre_routing("Wie findest du das neue Album?")
        assert agent == "chat_agent"

    def test_magst_du(self):
        agent, _ = _match_pre_routing("Magst du Jazz?")
        assert agent == "chat_agent"

    def test_deine_meinung(self):
        agent, _ = _match_pre_routing("Deine Meinung zu Berlin?")
        assert agent == "chat_agent"


class TestBotInstructionDeleteTrigger:
    def test_vergiss_die_instruktion(self):
        agent, label = _match_pre_routing("Vergiss die Instruktion von gestern")
        assert agent == "memory_agent"
        assert "bot-instruction" in label

    def test_alle_instruktionen_loeschen(self):
        agent, _ = _match_pre_routing("Alle Instruktionen löschen")
        assert agent == "memory_agent"

    def test_instruktionen_zuruecksetzen(self):
        agent, _ = _match_pre_routing("Instruktionen zurücksetzen bitte")
        assert agent == "memory_agent"

    def test_bot_instruction_before_memory_delete(self):
        # "vergiss die instruktion" muss vor generischem "vergiss " greifen
        agent, label = _match_pre_routing("Vergiss die Instruktion")
        assert agent == "memory_agent"
        assert "bot-instruction" in label


class TestMemoryDeleteTrigger:
    def test_vergiss_mit_leerzeichen(self):
        agent, label = _match_pre_routing("Vergiss dass ich Käse mag")
        assert agent == "memory_agent"
        assert "delete" in label

    def test_loesche_aus_dem_profil(self):
        agent, _ = _match_pre_routing("Lösche aus dem Profil: Lieblingsfarbe")
        assert agent == "memory_agent"

    def test_aus_meinem_profil_loeschen(self):
        agent, _ = _match_pre_routing("Aus meinem Profil löschen: Adresse")
        assert agent == "memory_agent"


class TestMemorySaveTrigger:
    def test_merke_dir_dass(self):
        agent, label = _match_pre_routing("Merke dir dass ich Veganer bin")
        assert agent == "memory_agent"
        assert "save" in label

    def test_speichere_dass(self):
        agent, _ = _match_pre_routing("Speichere dass mein Bruder Marco heißt")
        assert agent == "memory_agent"

    def test_von_jetzt_an(self):
        agent, _ = _match_pre_routing("Von jetzt an sollst du kürzer antworten")
        assert agent == "memory_agent"

    def test_fuege_hinzu(self):
        agent, _ = _match_pre_routing("Füge hinzu: ich esse kein Fleisch")
        assert agent == "memory_agent"


class TestNoMatch:
    def test_normal_question(self):
        result = _match_pre_routing("Wie ist das Wetter heute?")
        assert result is None

    def test_empty_string(self):
        result = _match_pre_routing("")
        assert result is None

    def test_unrelated_text(self):
        result = _match_pre_routing("Spiel mir Musik ab")
        assert result is None

    def test_partial_prefix_no_match(self):
        # "vergiss" ohne Leerzeichen/Komma danach soll nicht matchen
        result = _match_pre_routing("vergissen ist menschlich")
        assert result is None


class TestCaseInsensitivity:
    def test_uppercase_opinion(self):
        agent, _ = _match_pre_routing("WAS DENKST DU darüber?")
        assert agent == "chat_agent"

    def test_mixed_case_memory_save(self):
        agent, _ = _match_pre_routing("MERKE DIR DASS ich früh aufstehe")
        assert agent == "memory_agent"


class TestVergissPrefixBugFix:
    """Issue #96: "vergiss "-Prefix darf keine temporalen/negierten Sätze matchen."""

    def test_vergiss_morgen_nicht_no_match(self):
        """Temporal+Negation darf NICHT an memory_agent geroutet werden."""
        result = _match_pre_routing("Vergiss morgen nicht das Meeting um 10")
        assert result is None or result[0] != "memory_agent"

    def test_vergiss_nicht_dass_no_match(self):
        result = _match_pre_routing("Vergiss nicht dass du das Meeting hast")
        assert result is None or result[0] != "memory_agent"

    def test_vergiss_den_eintrag_matches(self):
        """Explizite Lösch-Absicht mit Artikel → memory_agent."""
        agent, _ = _match_pre_routing("Vergiss den Eintrag über Berlin")
        assert agent == "memory_agent"

    def test_vergiss_die_info_matches(self):
        agent, _ = _match_pre_routing("Vergiss die Info über das Projekt")
        assert agent == "memory_agent"

    def test_vergiss_alles_matches(self):
        agent, _ = _match_pre_routing("Vergiss alles was ich dir gesagt habe")
        assert agent == "memory_agent"

    def test_vergiss_bitte_matches(self):
        agent, _ = _match_pre_routing("Vergiss bitte den letzten Eintrag")
        assert agent == "memory_agent"

    def test_vergiss_das_matches(self):
        agent, _ = _match_pre_routing("Vergiss das bitte")
        assert agent == "memory_agent"
