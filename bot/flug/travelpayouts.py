"""
bot/flug/travelpayouts.py – Phase 247 (Issue #385)

Client für die Travelpayouts Data API (`aviasales/v3/prices_for_dates`).
Die Daten stammen aus dem Aviasales-Cache (Suchen anderer Nutzer) und können
einige Tage alt sein – für Alarm-Kandidaten reicht das, gebucht wird über den
Link nach eigener Prüfung.

Der Token geht ausschließlich als Header `X-Access-Token` raus, nie als
Query-Parameter: URLs landen in Logs und Exceptions, Header nicht.

Das Antwortschema ist ohne Token nicht verifizierbar (die Doku blockt
automatisierte Abrufe) – der Parser liest deshalb defensiv und verwirft
einzelne unbrauchbare Einträge statt der ganzen Antwort.
"""

import logging
import math
import re
from typing import Any

import httpx
from pydantic import ValidationError

from bot.flug.bereinigung import einzeilig
from bot.flug.modelle import Angebot
from bot.flug.netz import hole_json

logger = logging.getLogger(__name__)

_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
_LINK_BASIS = "https://www.aviasales.com"
_WAEHRUNG = "eur"
_LIMIT = 500
# Nur ein Pfad ohne Leer-/Steuerzeichen: Telegram verlinkt Klartext-URLs, ein
# Zeilenumbruch im API-Feld ergäbe sonst eine zweite, fremde klickbare Zeile.
# Live gemessen (20.11.2026): 335–388 Zeichen, auch mit 3 Umstiegen – 1000 lässt
# Luft und bleibt weit unter Telegrams 4096-Zeichen-Limit pro Nachricht.
_LINK_PFAD = re.compile(r"/[A-Za-z0-9/_\-.~%?=&+]{1,1000}")
_AIRLINE = re.compile(r"[A-Z0-9]{2}")


class TravelpayoutsFehler(RuntimeError):
    """Abruf oder Auswertung der Travelpayouts-Antwort ist fehlgeschlagen."""


async def lade_angebote(
    client: httpx.AsyncClient,
    origin: str,
    ziel: str,
    monat: str,
    hin_und_zurueck: bool,
    token: str,
) -> tuple[Angebot, ...]:
    """Lädt die günstigsten gecachten Preise für einen Abflugmonat (YYYY-MM)."""
    params = {
        "origin": origin,
        "destination": ziel,
        "departure_at": monat,
        "one_way": "false" if hin_und_zurueck else "true",
        "currency": _WAEHRUNG,
        "sorting": "price",
        "unique": "false",
        "direct": "false",
        "limit": str(_LIMIT),
    }
    daten = await hole_json(client, _URL, params=params, headers={"X-Access-Token": token}, fehler=TravelpayoutsFehler)
    return parse_antwort(daten)


def parse_antwort(daten: Any) -> tuple[Angebot, ...]:
    """Wandelt die API-Antwort in Angebote um; kaputte Einträge fallen raus."""
    if not isinstance(daten, dict):
        raise TravelpayoutsFehler("Antwort ist kein Objekt")
    if daten.get("success") is False:
        raise TravelpayoutsFehler(f"API meldet Fehler: {einzeilig(daten.get('error', ''), 200)}")
    waehrung = daten.get("currency")
    if waehrung is not None and str(waehrung).lower() != _WAEHRUNG:
        raise TravelpayoutsFehler(f"Unerwartete Währung: {einzeilig(waehrung, 10)}")
    eintraege = daten.get("data")
    if not isinstance(eintraege, list):
        raise TravelpayoutsFehler("Antwort ohne data-Liste")

    angebote = tuple(a for a in (_parse_eintrag(e) for e in eintraege) if a is not None)
    verworfen = len(eintraege) - len(angebote)
    if verworfen:
        logger.warning(f"Travelpayouts: {verworfen} von {len(eintraege)} Einträgen unbrauchbar")
    return angebote


def _parse_eintrag(eintrag: Any) -> Angebot | None:
    if not isinstance(eintrag, dict):
        return None
    try:
        preis = float(eintrag["price"])
        if not math.isfinite(preis):
            return None
        airline = str(eintrag.get("airline") or "")
        return Angebot(
            preis=round(preis),
            abflug=eintrag["departure_at"],
            rueckflug=eintrag.get("return_at") or None,
            umstiege=int(eintrag.get("transfers") or 0),
            umstiege_rueck=int(eintrag.get("return_transfers") or 0),
            airline=airline if _AIRLINE.fullmatch(airline) else "",
            link=_link(eintrag.get("link")),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError, ValidationError):
        return None


def _link(roh: Any) -> str:
    """Nur saubere relative Aviasales-Pfade werden zu Links – alles andere fällt weg."""
    if isinstance(roh, str) and _LINK_PFAD.fullmatch(roh) and not roh.startswith("//"):
        return _LINK_BASIS + roh
    return ""
