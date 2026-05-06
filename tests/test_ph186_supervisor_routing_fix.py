"""
tests/test_ph186_supervisor_routing_fix.py – Phase 186 (Issue #161)

Testet:
1. Quote-Stripping in _match_pre_routing (Anführungszeichen blockieren kein Matching mehr)
2. Neue Save-Trigger-Prefixes (merk dir das, notiere, bitte merke dir etc.)
"""

from agent.supervisor import _match_pre_routing


class TestQuoteStripping:
    """Anführungszeichen um die Nachricht dürfen Prefix-Matching nicht verhindern."""

    def test_quoted_merke_dir_dass(self):
        agent, label = _match_pre_routing('"Merke dir dass ich Veganer bin"')
        assert agent == "memory_agent"
        assert "save" in label

    def test_single_quoted_merke_dir_dass(self):
        agent, label = _match_pre_routing("'Merke dir dass ich Veganer bin'")
        assert agent == "memory_agent"

    def test_quoted_vergiss_den(self):
        agent, label = _match_pre_routing('"Vergiss den Eintrag über Berlin"')
        assert agent == "memory_agent"
        assert "delete" in label

    def test_quoted_foto(self):
        agent, label = _match_pre_routing('"[FOTO] Was ist das?"')
        assert agent == "vision_agent"

    def test_quote_only_outer_stripped(self):
        """Nur äußere Quotes werden gestrippt, innere bleiben."""
        agent, _ = _match_pre_routing('"Merke dir dass er "Chef" ist"')
        assert agent == "memory_agent"

    def test_no_match_still_none_with_quotes(self):
        result = _match_pre_routing('"Wie ist das Wetter heute?"')
        assert result is None


class TestNewSavePrefixes:
    """Neue Save-Trigger-Prefixes die in _PRE_ROUTING_RULES ergänzt wurden."""

    def test_merk_dir_das(self):
        agent, label = _match_pre_routing("Merk dir das bitte")
        assert agent == "memory_agent"
        assert "save" in label

    def test_merke_dir_das(self):
        agent, label = _match_pre_routing("Merke dir das")
        assert agent == "memory_agent"

    def test_merk_das(self):
        agent, label = _match_pre_routing("Merk das")
        assert agent == "memory_agent"

    def test_speichere_das(self):
        agent, _ = _match_pre_routing("Speichere das für später")
        assert agent == "memory_agent"

    def test_notiere_dass(self):
        agent, label = _match_pre_routing("Notiere dass ich kein Fleisch esse")
        assert agent == "memory_agent"
        assert "save" in label

    def test_notiere_das(self):
        agent, _ = _match_pre_routing("Notiere das")
        assert agent == "memory_agent"

    def test_notier_dass(self):
        agent, _ = _match_pre_routing("Notier dass mein Kollege Sven kommt")
        assert agent == "memory_agent"

    def test_notier_das(self):
        agent, _ = _match_pre_routing("Notier das für mich")
        assert agent == "memory_agent"

    def test_notiere_dir(self):
        agent, _ = _match_pre_routing("Notiere dir meine neue Adresse")
        assert agent == "memory_agent"

    def test_bitte_merke_dir(self):
        agent, label = _match_pre_routing("Bitte merke dir dass ich Frühaufsteher bin")
        assert agent == "memory_agent"
        assert "save" in label

    def test_bitte_merk_dir(self):
        agent, _ = _match_pre_routing("Bitte merk dir meine Telefonnummer")
        assert agent == "memory_agent"

    def test_case_insensitive_notiere(self):
        agent, _ = _match_pre_routing("NOTIERE DASS ich Sport mache")
        assert agent == "memory_agent"

    def test_quoted_notiere(self):
        agent, _ = _match_pre_routing('"Notiere dass ich morgen frei habe"')
        assert agent == "memory_agent"


class TestExistingPrefixesStillWork:
    """Regression: bestehende Prefixes funktionieren weiterhin."""

    def test_merke_dir_dass(self):
        agent, _ = _match_pre_routing("Merke dir dass ich in Berlin wohne")
        assert agent == "memory_agent"

    def test_speichere_dass(self):
        agent, _ = _match_pre_routing("Speichere dass mein Bruder Marco heißt")
        assert agent == "memory_agent"

    def test_von_jetzt_an(self):
        agent, _ = _match_pre_routing("Von jetzt an sollst du kürzer antworten")
        assert agent == "memory_agent"

    def test_vergiss_den(self):
        agent, _ = _match_pre_routing("Vergiss den Eintrag über meine Adresse")
        assert agent == "memory_agent"

    def test_vergiss_die_instruktion(self):
        agent, label = _match_pre_routing("Vergiss die Instruktion von gestern")
        assert agent == "memory_agent"
        assert "bot-instruction" in label
