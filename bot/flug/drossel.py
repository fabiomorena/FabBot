"""
bot/flug/drossel.py – Phase 247 (Issue #385)

Sliding-Window-Drossel pro Schlüssel (User-ID), speicherbegrenzt. Schützt den
kostenlosen Travelpayouts-Account vor Request-Schleifen und den Bot vor
Antwort-Spam an Fremde.
"""

import time
from collections.abc import Callable


class Drossel:
    """Erlaubt höchstens `max_anzahl` Ereignisse pro Schlüssel im Zeitfenster."""

    def __init__(
        self,
        max_anzahl: int,
        fenster_sekunden: float,
        max_schluessel: int = 1000,
        uhr: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_anzahl
        self._fenster = fenster_sekunden
        self._max_schluessel = max_schluessel
        self._uhr = uhr
        self._treffer: dict[int, tuple[float, ...]] = {}

    def erlaube(self, schluessel: int) -> bool:
        """True = Ereignis zählt und ist erlaubt; False = Limit erreicht."""
        jetzt = self._uhr()
        frisch = tuple(t for t in self._treffer.get(schluessel, ()) if jetzt - t < self._fenster)
        rest = {k: v for k, v in self._treffer.items() if k != schluessel}
        if len(frisch) >= self._max:
            self._treffer = {**rest, schluessel: frisch}
            return False
        if len(rest) >= self._max_schluessel:
            # Älteste Schlüssel (Einfügereihenfolge) fallen raus – Speicher bleibt begrenzt.
            rest = dict(list(rest.items())[len(rest) - self._max_schluessel + 1 :])
        self._treffer = {**rest, schluessel: (*frisch, jetzt)}
        return True
