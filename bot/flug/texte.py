"""
bot/flug/texte.py – Phase 247 (Issue #385)

Nachrichtentexte des Flug-Bots. Bewusst Klartext ohne parse_mode: Städte-
und Airline-Namen kommen aus einer externen API und dürfen kein Markdown
brechen (vgl. Telegram-Markdown-Falle).
"""

import calendar
from datetime import date, datetime

from bot.flug.modelle import Angebot, Watch

_LINK_BASIS = "https://www.aviasales.com"
_WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_MONATSNAMEN = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip
CACHE_HINWEIS = "Preis aus dem Aviasales-Cache, kann einige Tage alt sein – vor dem Buchen auf der Seite prüfen."


def zeitraum_text(von: date, bis: date) -> str:
    def monat(d: date) -> str:
        return _MONATSNAMEN[d.month - 1]

    ganze_monate = von.day == 1 and bis.day == calendar.monthrange(bis.year, bis.month)[1]
    if ganze_monate:
        if (von.year, von.month) == (bis.year, bis.month):
            return f"{monat(von)} {von.year}"
        if von.year == bis.year:
            return f"{monat(von)}–{monat(bis)} {bis.year}"
        return f"{monat(von)} {von.year}–{monat(bis)} {bis.year}"
    if von == bis:
        return f"{von:%d.%m.%Y}"
    if von.year == bis.year:
        return f"{von:%d.%m.}–{bis:%d.%m.%Y}"
    return f"{von:%d.%m.%Y}–{bis:%d.%m.%Y}"


def art_text(watch: Watch) -> str:
    if not watch.hin_und_zurueck:
        return "nur hin"
    lo, hi = watch.naechte_min, watch.naechte_max
    if lo is None and hi is None:
        return "hin & zurück"
    if lo is None:
        return f"hin & zurück, bis {hi} Nächte"
    if hi is None:
        return f"hin & zurück, ab {lo} Nächte"
    if lo == hi:
        return f"hin & zurück, {lo} Nächte"
    return f"hin & zurück, {lo}–{hi} Nächte"


def watch_text(watch: Watch) -> str:
    zuletzt = f", zuletzt {watch.letzter_preis} €" if watch.letzter_preis is not None else ""
    return (
        f"{watch.origin.anzeige()} → {watch.ziel.anzeige()}\n"
        f"{zeitraum_text(watch.von, watch.bis)}, {art_text(watch)}, bis {watch.max_preis} €{zuletzt}"
    )


def liste_text(watches: tuple[Watch, ...]) -> str:
    if not watches:
        return "Du hast keine aktiven Suchen. Neue Suche mit /neu."
    eintraege = "\n\n".join(f"{i}. {watch_text(w)}" for i, w in enumerate(watches, start=1))
    return f"Deine Suchen:\n\n{eintraege}"


def _zeit(dt: datetime) -> str:
    return f"{_WOCHENTAGE[dt.weekday()]} {dt:%d.%m.%Y %H:%M}"


def _umstiege(n: int) -> str:
    if n == 0:
        return "direkt"
    return "1 Umstieg" if n == 1 else f"{n} Umstiege"


def suchlink(watch: Watch, angebot: Angebot) -> str:
    """Aviasales-Suchlink als Ersatz, wenn die API keinen Link liefert."""
    rueck = f"{angebot.rueckflug:%d%m}" if angebot.rueckflug else ""
    return f"{_LINK_BASIS}/search/{watch.origin.code}{angebot.abflug:%d%m}{watch.ziel.code}{rueck}1"


def alert_text(watch: Watch, angebot: Angebot) -> str:
    art = "hin & zurück" if watch.hin_und_zurueck else "nur hin"
    zeilen = [
        f"✈️ Preisalarm: {watch.origin.name} → {watch.ziel.name}",
        f"{angebot.preis} € ({art})",
        f"Hin: {_zeit(angebot.abflug)}, {_umstiege(angebot.umstiege)}",
    ]
    if angebot.rueckflug is not None:
        zeilen.append(f"Zurück: {_zeit(angebot.rueckflug)}, {_umstiege(angebot.umstiege_rueck)}")
    if angebot.airline:
        zeilen.append(f"Airline: {angebot.airline}")
    zeilen.append(f"Deine Grenze: {watch.max_preis} €")
    link = angebot.link or suchlink(watch, angebot)
    return "\n".join(zeilen) + f"\n\n{link}\n\n{CACHE_HINWEIS}"


def stand_text(watch: Watch, angebot: Angebot | None) -> str:
    """Ergebnis der Sofortprüfung, wenn kein Alarm fällig ist."""
    if angebot is None:
        return (
            "Im Cache gibt es gerade keinen passenden Preis. "
            f"Ich prüfe regelmäßig und melde mich, sobald etwas unter {watch.max_preis} € auftaucht."
        )
    return (
        f"Aktuell günstigster passender Preis: {angebot.preis} € – über deiner Grenze von "
        f"{watch.max_preis} €. Ich melde mich, sobald es günstiger wird."
    )


def abgelaufen_text(watch: Watch) -> str:
    return f"⌛ Suche abgelaufen und entfernt:\n{watch_text(watch)}"
