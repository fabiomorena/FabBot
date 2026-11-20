"""
bot/flug/auswahl.py – Phase 247 (Issue #385)

Welche Angebote passen zu einer Suche, und wann wird alarmiert.

Alarm-Regel: Der günstigste passende Preis liegt unter der Grenze **und**
unter dem zuletzt gemeldeten Preis. Gleichbleibende Preise lösen also keinen
zweiten Alarm aus. Steigt der Preis über die Grenze, wird die Meldung
zurückgesetzt – fällt er danach wieder darunter, gibt es erneut einen Alarm.
Kommt gar kein Angebot zurück (Cache-Lücke), bleibt die Meldung bestehen,
sonst würde jede Lücke einen Wiederholungsalarm erzeugen.
"""

from datetime import date

from bot.flug.modelle import Angebot, Watch


def passende_angebote(angebote: tuple[Angebot, ...], watch: Watch, heute: date) -> tuple[Angebot, ...]:
    """Filtert auf Abflugfenster, Reiseart und Nächte; sortiert nach Preis."""
    frueheste = max(watch.von, heute)

    def passt(a: Angebot) -> bool:
        tag = a.abflug.date()
        if not frueheste <= tag <= watch.bis:
            return False
        if not watch.hin_und_zurueck:
            return a.rueckflug is None
        if a.rueckflug is None:
            return False
        naechte = (a.rueckflug.date() - tag).days
        if naechte < 0:
            return False
        if watch.naechte_min is not None and naechte < watch.naechte_min:
            return False
        return watch.naechte_max is None or naechte <= watch.naechte_max

    return tuple(sorted((a for a in angebote if passt(a)), key=lambda a: (a.preis, a.abflug)))


def bewerte(watch: Watch, bestes: Angebot | None) -> tuple[Watch, bool]:
    """Liefert die aktualisierte Suche und ob alarmiert werden soll."""
    if bestes is None:
        return watch.model_copy(update={"letzter_preis": None}), False

    preis = bestes.preis
    if preis > watch.max_preis:
        return watch.model_copy(update={"letzter_preis": preis, "gemeldeter_preis": None}), False

    alarm = watch.gemeldeter_preis is None or preis < watch.gemeldeter_preis
    gemeldet = preis if alarm else watch.gemeldeter_preis
    return watch.model_copy(update={"letzter_preis": preis, "gemeldeter_preis": gemeldet}), alarm
