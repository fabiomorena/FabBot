"""
Tests für _ConflictTracker – Toleranz gegen transiente Telegram-Conflicts.

Hintergrund (12.08.2026): Der Error-Handler beendete den Prozess beim ERSTEN
Conflict. Nach einem Neustart hält Telegram die getUpdates-Verbindung der
alten Instanz aber noch ~60s offen – die neue Instanz bekommt dadurch einen
Conflict, den niemand verursacht hat. Aus dem sofortigen Exit wurde so eine
Neustart-Schleife: 21:41:39 Conflict -> Exit -> Neustart -> 21:42:55 Conflict.
"""

from bot.conflict_tracker import ConflictTracker


class _Uhr:
    """Steuerbarer Zeitgeber für die Tests."""

    def __init__(self):
        self.jetzt = 1000.0

    def __call__(self) -> float:
        return self.jetzt

    def vor(self, sekunden: float) -> None:
        self.jetzt += sekunden


class TestConflictTracker:
    def test_erster_conflict_beendet_nicht(self):
        """Ein einzelner Conflict ist meist transient – kein Exit."""
        t = ConflictTracker(schwelle=3, fenster_sekunden=300, zeitgeber=_Uhr())
        assert t.registriere() is False

    def test_schwelle_im_fenster_beendet(self):
        uhr = _Uhr()
        t = ConflictTracker(schwelle=3, fenster_sekunden=300, zeitgeber=uhr)
        assert t.registriere() is False
        uhr.vor(70)
        assert t.registriere() is False
        uhr.vor(70)
        assert t.registriere() is True

    def test_alte_conflicts_fallen_aus_dem_fenster(self):
        """Vereinzelte Conflicts über Stunden dürfen sich nicht aufsummieren."""
        uhr = _Uhr()
        t = ConflictTracker(schwelle=3, fenster_sekunden=300, zeitgeber=uhr)
        for _ in range(5):
            assert t.registriere() is False
            uhr.vor(301)

    def test_genau_am_fensterrand_zaehlt_noch(self):
        uhr = _Uhr()
        t = ConflictTracker(schwelle=2, fenster_sekunden=300, zeitgeber=uhr)
        assert t.registriere() is False
        uhr.vor(300)
        assert t.registriere() is True

    def test_schwelle_eins_beendet_sofort(self):
        """Rückwärtskompatibel: schwelle=1 entspricht dem alten Verhalten."""
        t = ConflictTracker(schwelle=1, fenster_sekunden=300, zeitgeber=_Uhr())
        assert t.registriere() is True

    def test_zaehler_nach_ausloesen_zurueckgesetzt(self):
        """Nach dem Auslösen beginnt ein frisches Fenster."""
        uhr = _Uhr()
        t = ConflictTracker(schwelle=2, fenster_sekunden=300, zeitgeber=uhr)
        t.registriere()
        assert t.registriere() is True
        assert t.registriere() is False

    def test_anzahl_im_fenster_wird_gemeldet(self):
        """Für die Log-Ausgabe: wieviele Conflicts stecken im Fenster."""
        uhr = _Uhr()
        t = ConflictTracker(schwelle=3, fenster_sekunden=300, zeitgeber=uhr)
        t.registriere()
        uhr.vor(10)
        t.registriere()
        assert t.anzahl_im_fenster == 2
