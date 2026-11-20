"""
bot/flug/zeitraum.py – Phase 247 (Issue #385)

Regelbasierter Parser für Reisezeiträume – bewusst ohne LLM, damit die
Eingabe deterministisch und testbar bleibt.

Verstanden werden ganze Monate ("März 2027", "03/2027", "2027-03",
"marzo"), Monatsbereiche ("März bis April") und Datumsbereiche
("15.03.-30.03.2027"). Fehlt das Jahr, gilt das nächste Vorkommen.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

MAX_TAGE = 92
MAX_VORLAUF_TAGE = 365
_MAX_LAENGE = 60

# Deutsch + Italienisch (zweisprachiger Haushalt), jeweils mit Kurzformen.
_MONATE = {
    "januar": 1, "jan": 1, "jänner": 1, "gennaio": 1, "gen": 1,
    "februar": 2, "feb": 2, "febbraio": 2,
    "märz": 3, "maerz": 3, "mär": 3, "mar": 3, "marzo": 3,
    "april": 4, "apr": 4, "aprile": 4,
    "mai": 5, "maggio": 5, "mag": 5,
    "juni": 6, "jun": 6, "giugno": 6, "giu": 6,
    "juli": 7, "jul": 7, "luglio": 7, "lug": 7,
    "august": 8, "aug": 8, "agosto": 8, "ago": 8,
    "september": 9, "sep": 9, "sept": 9, "settembre": 9, "set": 9,
    "oktober": 10, "okt": 10, "ottobre": 10, "ott": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "dezember": 12, "dez": 12, "dicembre": 12, "dic": 12,
}  # fmt: skip

_TRENNER = re.compile(r"\s*(?:–|—|-|\bbis\b)\s*")
_ISO_MONAT = re.compile(r"^(\d{4})-(\d{1,2})$")
# Punkt nach dem Monat optional ("01.01-31.01.2027"), vor dem Jahr Pflicht –
# so bleibt "03.2027" ein Monat (März 2027) und wird kein Datum.
_DATUM = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4})?)?$")
_MONAT_ZAHL = re.compile(r"^(\d{1,2})[./](\d{4})$")
_MONAT_NAME = re.compile(r"^([a-zäöü]+)\.?(?:\s+(\d{4}))?$")


class ZeitraumFehler(ValueError):
    """Eingabe ist kein gültiger Reisezeitraum. Die Meldung ist nutzertauglich."""


@dataclass(frozen=True)
class Zeitraum:
    von: date
    bis: date


@dataclass(frozen=True)
class _Teil:
    tag: int | None  # None = ganzer Monat
    monat: int
    jahr: int | None


def parse_zeitraum(text: str, heute: date) -> Zeitraum:
    """Wandelt eine Freitext-Eingabe in einen Abflug-Zeitraum um."""
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not norm or len(norm) > _MAX_LAENGE:
        raise ZeitraumFehler("Das Format verstehe ich nicht.")

    if m := _ISO_MONAT.match(norm):
        teile: tuple[_Teil, ...] = (_teil(None, int(m[2]), int(m[1])),)
    else:
        stuecke = [s for s in _TRENNER.split(norm) if s]
        if len(stuecke) not in (1, 2):
            raise ZeitraumFehler("Das Format verstehe ich nicht.")
        teile = tuple(_parse_teil(s) for s in stuecke)

    von, bis = _aufloesen(teile, heute)
    return _validiere(von, bis, heute)


def monate(von: date, bis: date) -> tuple[str, ...]:
    """Alle Monate (YYYY-MM), die der Zeitraum anschneidet."""
    start = von.year * 12 + von.month - 1
    ende = bis.year * 12 + bis.month - 1
    return tuple(f"{i // 12}-{i % 12 + 1:02d}" for i in range(start, ende + 1))


# ── Intern ────────────────────────────────────────────────────────────────────


def _teil(tag: int | None, monat: int, jahr: int | None) -> _Teil:
    if not 1 <= monat <= 12:
        raise ZeitraumFehler("Diesen Monat gibt es nicht.")
    return _Teil(tag=tag, monat=monat, jahr=jahr)


def _parse_teil(text: str) -> _Teil:
    if m := _DATUM.match(text):
        jahr = int(m[3]) if m[3] else None
        if jahr is not None and jahr < 100:
            jahr += 2000
        return _teil(int(m[1]), int(m[2]), jahr)
    if m := _MONAT_ZAHL.match(text):
        return _teil(None, int(m[1]), int(m[2]))
    if (m := _MONAT_NAME.match(text)) and m[1] in _MONATE:
        return _teil(None, _MONATE[m[1]], int(m[2]) if m[2] else None)
    raise ZeitraumFehler("Das Format verstehe ich nicht.")


def _anfang(t: _Teil, jahr: int) -> date:
    return _datum(jahr, t.monat, t.tag or 1)


def _ende(t: _Teil, jahr: int) -> date:
    return _datum(jahr, t.monat, t.tag or calendar.monthrange(jahr, t.monat)[1])


def _datum(jahr: int, monat: int, tag: int) -> date:
    try:
        return date(jahr, monat, tag)
    except ValueError:
        raise ZeitraumFehler("Dieses Datum gibt es nicht.") from None


def _naechstes_jahr(t: _Teil, heute: date) -> int:
    return heute.year if _ende(t, heute.year) >= heute else heute.year + 1


def _aufloesen(teile: tuple[_Teil, ...], heute: date) -> tuple[date, date]:
    if len(teile) == 1:
        (t,) = teile
        jahr = t.jahr if t.jahr is not None else _naechstes_jahr(t, heute)
        return _anfang(t, jahr), _ende(t, jahr)

    a, b = teile
    b_nicht_vor_a = (b.monat, b.tag or 31) >= (a.monat, a.tag or 1)
    if a.jahr is not None and b.jahr is not None:
        ja, jb = a.jahr, b.jahr
    elif b.jahr is not None:
        jb = b.jahr
        ja = jb if b_nicht_vor_a else jb - 1
    elif a.jahr is not None:
        ja = a.jahr
        jb = ja if b_nicht_vor_a else ja + 1
    else:
        # Ohne Jahresangabe zählt das Ende: "Oktober bis Dezember" am 20.11.
        # meint das laufende Jahr (Anfang wird auf heute gekürzt), nicht 2027.
        jb = _naechstes_jahr(b, heute)
        ja = jb if b_nicht_vor_a else jb - 1
    return _anfang(a, ja), _ende(b, jb)


def _validiere(von: date, bis: date, heute: date) -> Zeitraum:
    if bis < von:
        raise ZeitraumFehler("Das Ende liegt vor dem Anfang.")
    if bis < heute:
        raise ZeitraumFehler("Der Zeitraum liegt in der Vergangenheit.")
    von = max(von, heute)
    if (bis - von).days + 1 > MAX_TAGE:
        raise ZeitraumFehler("Der Zeitraum darf höchstens 3 Monate umfassen.")
    if bis > heute + timedelta(days=MAX_VORLAUF_TAGE):
        raise ZeitraumFehler("Preise gibt es nur für die nächsten 12 Monate.")
    return Zeitraum(von=von, bis=bis)
