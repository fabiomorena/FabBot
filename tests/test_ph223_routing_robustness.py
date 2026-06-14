"""
tests/test_ph223_routing_robustness.py – Phase 223 (Issue #280)

Routing/Intent-Robustheit. Bisher matchte das Pre-Routing nur via
``startswith`` – eindeutige Trigger mitten im Satz (z.B. "Hey FabBot, wie viel
cpu ...") wurden verfehlt. Diese Tests sichern wortgrenzen-basiertes Matching
für eine kuratierte Menge eindeutiger Mehrwort-Phrasen ab, ohne dass nackte
Tokens (cpu/ram) False-Positives erzeugen. Zusätzlich misst ein Golden-Set die
Routing-Trefferquote.
"""

from agent.supervisor import _match_pre_routing


class TestMidSentenceTriggers:
    """Eindeutige Trigger müssen auch mitten im Satz greifen (Kern von #280)."""

    def test_cpu_question_after_greeting(self):
        agent, _ = _match_pre_routing("Hey FabBot, wie viel cpu brauchen wir gerade?")
        assert agent == "system_agent"

    def test_ram_question_mid_sentence(self):
        agent, _ = _match_pre_routing("Sag mal, wie viel ram ist noch frei?")
        assert agent == "system_agent"

    def test_system_status_mid_sentence(self):
        agent, _ = _match_pre_routing("Kannst du mir den system status zeigen?")
        assert agent == "system_agent"

    def test_opinion_after_filler(self):
        agent, _ = _match_pre_routing("Sag mal, was denkst du darüber?")
        assert agent == "chat_agent"

    def test_wie_findest_du_mid_sentence(self):
        agent, _ = _match_pre_routing("Und wie findest du das neue Album?")
        assert agent == "chat_agent"

    def test_deine_meinung_mid_sentence(self):
        agent, _ = _match_pre_routing("Mich würde deine Meinung dazu interessieren")
        assert agent == "chat_agent"


class TestNoFalsePositives:
    """Nackte Tokens dürfen nicht ungewollt routen – Status quo darf sich nicht verschlechtern."""

    def test_cpu_explanation_not_system(self):
        # Wissensfrage über CPU, keine Status-Abfrage
        result = _match_pre_routing("Erklär mir wie eine CPU funktioniert")
        assert result is None or result[0] != "system_agent"

    def test_instagram_not_system(self):
        # "ram" steckt in Instagram – darf nicht system_agent triggern
        result = _match_pre_routing("Ich habe gerade Instagram installiert")
        assert result is None or result[0] != "system_agent"

    def test_program_not_system(self):
        result = _match_pre_routing("Das Programm läuft stabil")
        assert result is None or result[0] != "system_agent"

    def test_normal_question_still_none(self):
        assert _match_pre_routing("Wie ist das Wetter heute?") is None

    def test_vergiss_morgen_nicht_still_none(self):
        # Issue #96 darf nicht regressieren
        result = _match_pre_routing("Vergiss morgen nicht das Meeting um 10")
        assert result is None or result[0] != "memory_agent"


# Golden-Set: (Nachricht, erwarteter Agent oder None für LLM-Routing).
# None = bewusst kein deterministisches Pre-Routing, geht an den LLM-Supervisor.
_GOLDEN_SET: list[tuple[str, str | None]] = [
    # System-Stats – Satzanfang und mitten im Satz
    ("CPU?", "system_agent"),
    ("Wie viel RAM ist frei?", "system_agent"),
    ("Hey, wie viel cpu brauchen wir gerade?", "system_agent"),
    ("Zeig mir mal den system status", "system_agent"),
    ("Und wie viel speicher ist noch übrig?", "system_agent"),
    # Opinion → chat_agent
    ("Was denkst du darüber?", "chat_agent"),
    ("Sag mal, was hälst du von Python?", "chat_agent"),
    ("Magst du Jazz?", "chat_agent"),
    ("Mich würde deine Meinung dazu interessieren", "chat_agent"),
    ("Und wie findest du das neue Album?", "chat_agent"),
    # Memory save/delete (bleiben prefix-getrieben)
    ("Merke dir dass ich Veganer bin", "memory_agent"),
    ("Speichere dass mein Bruder Marco heißt", "memory_agent"),
    ("Vergiss dass ich Käse mag", "memory_agent"),
    ("Lösche aus dem Profil: Lieblingsfarbe", "memory_agent"),
    ("Vergiss die Instruktion von gestern", "memory_agent"),
    # System-Marker
    ("[foto] zeig mir was drauf ist", "vision_agent"),
    ("[musik-analyse] analysiere diesen track", "music_analysis_agent"),
    ("[standort] 52.52, 13.40", "chat_agent"),
    # LLM-Routing erwartet (kein deterministisches Pre-Routing)
    ("Wie ist das Wetter heute?", None),
    ("Erklär mir wie eine CPU funktioniert", None),
    ("Schreib eine Mail an Marco", None),
    ("Was steht morgen im Kalender?", None),
    ("Ich habe gerade Instagram installiert", None),
    ("Erstelle eine Datei test.txt", None),
    ("Such mir Infos über Berlin", None),
    ("Spiel mir Musik ab", None),
    ("Das Programm läuft stabil", None),
    ("Wie geht es dir?", None),
    ("Mach einen Screenshot", None),
    ("Öffne die README", None),
]


class TestRoutingEvalHarness:
    """Misst die Trefferquote des deterministischen Pre-Routings gegen ein Golden-Set."""

    def test_golden_set_size(self):
        assert len(_GOLDEN_SET) >= 30

    def test_routing_accuracy(self):
        misses = []
        for text, expected in _GOLDEN_SET:
            result = _match_pre_routing(text)
            actual = result[0] if result else None
            if actual != expected:
                misses.append((text, expected, actual))
        accuracy = (len(_GOLDEN_SET) - len(misses)) / len(_GOLDEN_SET)
        assert accuracy == 1.0, f"Routing-Trefferquote {accuracy:.0%}, Fehler: {misses}"
