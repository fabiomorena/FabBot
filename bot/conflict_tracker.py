"""
Toleranz gegen transiente Telegram-Conflicts.

Ein Conflict bedeutet: eine andere Instanz pollt getUpdates mit demselben
Token. Das ist aber nicht immer eine echte Zweitinstanz – nach einem Neustart
hält Telegram die Verbindung der *eigenen* alten Instanz noch etwa eine Minute
offen. Wer beim ersten Conflict sofort beendet, erzeugt daraus eine
Neustart-Schleife: Exit -> launchd startet neu -> alte Verbindung immer noch
offen -> Conflict -> Exit ...

Genau das passierte am 12.08.2026 (21:41:39 und 21:42:55, jeweils ~70s nach
dem Start). Deshalb wird erst beendet, wenn sich Conflicts in einem Zeitfenster
häufen – dann liegt tatsächlich eine dauerhafte Zweitinstanz vor.
"""

from __future__ import annotations

import time
from collections.abc import Callable

# Nach wievielen Conflicts im Fenster von einer echten Zweitinstanz ausgegangen
# wird. Zwei aufeinanderfolgende transiente Conflicts (wie am 12.08.) bleiben
# damit folgenlos.
STANDARD_SCHWELLE = 3

# Fenstergröße. Großzügig gegenüber dem beobachteten Abstand von ~70s, damit
# eine echte Zweitinstanz zuverlässig erkannt wird.
STANDARD_FENSTER_SEKUNDEN = 300


class ConflictTracker:
    """Zählt Conflicts innerhalb eines gleitenden Zeitfensters."""

    def __init__(
        self,
        schwelle: int = STANDARD_SCHWELLE,
        fenster_sekunden: float = STANDARD_FENSTER_SEKUNDEN,
        zeitgeber: Callable[[], float] = time.monotonic,
    ) -> None:
        self._schwelle = schwelle
        self._fenster = fenster_sekunden
        self._zeitgeber = zeitgeber
        self._zeitpunkte: list[float] = []

    @property
    def anzahl_im_fenster(self) -> int:
        return len(self._zeitpunkte)

    def registriere(self) -> bool:
        """Vermerkt einen Conflict.

        Liefert True, wenn die Schwelle im Fenster erreicht ist und der Prozess
        beendet werden sollte. Nach dem Auslösen beginnt ein frisches Fenster.
        """
        jetzt = self._zeitgeber()
        grenze = jetzt - self._fenster
        self._zeitpunkte = [t for t in self._zeitpunkte if t >= grenze]
        self._zeitpunkte.append(jetzt)

        if len(self._zeitpunkte) >= self._schwelle:
            self._zeitpunkte = []
            return True
        return False
