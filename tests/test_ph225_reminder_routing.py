"""
tests/test_ph225_reminder_routing.py – Phase 225 (Issue #286)

Negierte Erinnerungen ("Vergiss nicht X", "Nicht vergessen: X", "Denk dran: X")
wurden vom LLM-Supervisor nicht-deterministisch geroutet (mal memory_agent, mal
reminder_agent). Deterministisches Pre-Routing → reminder_agent macht das stabil.
Ein Guard verhindert, dass echte Memory-Fakten ("Vergiss nicht dass ich Jazz mag")
fälschlich als Erinnerung interpretiert werden.
"""

from langchain_core.messages import HumanMessage

from agent.supervisor import _pre_route_reminder, _match_pre_routing


def _route(text: str):
    routing = [HumanMessage(content=text)]
    return _pre_route_reminder({}, [], routing)


class TestNegationReminder:
    def test_vergiss_morgen_nicht_meeting(self):
        assert _route("Vergiss morgen nicht das Meeting um 10") == "reminder_agent"

    def test_vergiss_nicht_meeting(self):
        assert _route("Vergiss nicht das Meeting") == "reminder_agent"

    def test_vergiss_heute_nicht(self):
        assert _route("Vergiss heute nicht die Tabletten") == "reminder_agent"

    def test_nicht_vergessen_doppelpunkt(self):
        assert _route("Nicht vergessen: Müll rausbringen") == "reminder_agent"

    def test_denk_dran(self):
        assert _route("Denk dran, Marco anzurufen") == "reminder_agent"

    def test_denk_daran(self):
        assert _route("Denk daran die Rechnung zu zahlen") == "reminder_agent"


class TestMemoryFactGuard:
    """'Vergiss nicht dass ich ...' ist ein Profil-Fakt, keine Erinnerung."""

    def test_vergiss_nicht_dass_ich_praeferenz(self):
        assert _route("Vergiss nicht dass ich Jazz mag") is None

    def test_vergiss_nicht_dass_mein_fakt(self):
        assert _route("Vergiss nicht dass mein Bruder Marco heißt") is None


class TestNoFalsePositives:
    def test_normal_question(self):
        assert _route("Wie ist das Wetter heute?") is None

    def test_memory_delete_not_reminder(self):
        # echter Lösch-Befehl darf nicht als Reminder pre-geroutet werden
        assert _route("Vergiss den Eintrag über Berlin") is None

    def test_ich_denke_nicht_reminder(self):
        assert _route("Ich denke das passt schon") is None


class TestNoRegressionPreRouting:
    """Bestehendes Pre-Routing (#96, #280) bleibt unangetastet."""

    def test_vergiss_morgen_nicht_no_memory_delete(self):
        # darf weiterhin nicht als memory-delete matchen (Issue #96)
        result = _match_pre_routing("Vergiss morgen nicht das Meeting um 10")
        assert result is None or result[0] != "memory_agent"

    def test_memory_delete_still_works(self):
        agent, _ = _match_pre_routing("Vergiss den Eintrag über Berlin")
        assert agent == "memory_agent"
