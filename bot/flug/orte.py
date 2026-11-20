"""
bot/flug/orte.py – Phase 247 (Issue #385)

Ortssuche über die Travelpayouts-Autocomplete-API (`places2`). Braucht keinen
Token. Liefert Städte und Flughäfen mit IATA-Code.
"""

import re
from typing import Any

import httpx
from pydantic import ValidationError

from bot.flug.bereinigung import einzeilig, sieht_aus_wie_link
from bot.flug.modelle import Ort
from bot.flug.netz import hole_json

_URL = "https://autocomplete.travelpayouts.com/places2"
_MAX_TREFFER = 5
_TIMEOUT = 10.0
_CODE = re.compile(r"[A-Z]{3}")


class OrtsFehler(RuntimeError):
    """Ortssuche nicht möglich (ungültige Eingabe oder API nicht erreichbar)."""


async def suche_orte(term: str, *, client: httpx.AsyncClient | None = None) -> tuple[Ort, ...]:
    """Sucht Städte/Flughäfen zu einem Freitext, höchstens fünf, ohne Dubletten."""
    suchwort = (term or "").strip()
    if not 2 <= len(suchwort) <= 60:
        raise OrtsFehler("Suchbegriff muss 2–60 Zeichen lang sein")

    params = [("term", suchwort), ("locale", "de"), ("types[]", "city"), ("types[]", "airport")]
    if client is None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as eigener:
            daten = await hole_json(eigener, _URL, params=params, fehler=OrtsFehler)
    else:
        daten = await hole_json(client, _URL, params=params, fehler=OrtsFehler)
    if not isinstance(daten, list):
        raise OrtsFehler("Antwort ist keine Liste")
    return _auswerten(daten)


def _auswerten(daten: list[Any]) -> tuple[Ort, ...]:
    orte: dict[str, Ort] = {}
    for eintrag in daten:
        ort = _parse_ort(eintrag)
        if ort is not None and ort.code not in orte:
            orte = {**orte, ort.code: ort}
        if len(orte) == _MAX_TREFFER:
            break
    return tuple(orte.values())


def _parse_ort(eintrag: Any) -> Ort | None:
    if not isinstance(eintrag, dict):
        return None
    code = str(eintrag.get("code") or "")
    if not _CODE.fullmatch(code):
        return None
    # Namen landen als Klartext in Telegram – Zeilenumbrüche und Domains
    # würden dort zu klickbaren fremden Links (Phishing über API-Daten).
    name = einzeilig(eintrag.get("name") or "", 80)
    land = einzeilig(eintrag.get("country_name") or "", 80)
    try:
        return Ort(
            code=code,
            name=name if name and not sieht_aus_wie_link(name) else code,
            land="" if sieht_aus_wie_link(land) else land,
        )
    except ValidationError:
        return None
