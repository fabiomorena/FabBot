"""
tests/flug_testdaten.py – Phase 247 (Issue #385)

Gemeinsame Testdaten für die Flug-Tracker-Tests.
"""

from datetime import date, datetime, timezone

from bot.flug.modelle import Angebot, Ort, Watch

BERLIN = Ort(code="BER", name="Berlin", land="Deutschland")
ROM = Ort(code="ROM", name="Rom", land="Italien")


def watch(**abweichungen) -> Watch:
    basis = {
        "id": "ab12cd34",
        "user_id": 111,
        "chat_id": 111,
        "origin": BERLIN,
        "ziel": ROM,
        "von": date(2027, 3, 1),
        "bis": date(2027, 3, 31),
        "hin_und_zurueck": True,
        "naechte_min": None,
        "naechte_max": None,
        "max_preis": 150,
        "angelegt": datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
    }
    return Watch(**{**basis, **abweichungen})


def angebot(
    preis: int = 89,
    abflug: str = "2027-03-05T06:10:00+01:00",
    rueckflug: str | None = "2027-03-10T20:00:00+01:00",
    **abweichungen,
) -> Angebot:
    basis = {
        "preis": preis,
        "abflug": datetime.fromisoformat(abflug),
        "rueckflug": datetime.fromisoformat(rueckflug) if rueckflug else None,
        "airline": "FR",
        "link": "https://www.aviasales.com/search/BER0503ROM10031",
    }
    return Angebot(**{**basis, **abweichungen})


def eintrag(**abweichungen) -> dict:
    """Ein Eintrag im Format von aviasales/v3/prices_for_dates."""
    basis = {
        "origin": "BER",
        "destination": "ROM",
        "origin_airport": "BER",
        "destination_airport": "FCO",
        "price": 89,
        "airline": "FR",
        "flight_number": "8571",
        "departure_at": "2027-03-05T06:10:00+01:00",
        "return_at": "2027-03-10T20:00:00+01:00",
        "transfers": 0,
        "return_transfers": 1,
        "duration": 250,
        "link": "/search/BER0503ROM10031?t=FR1",
    }
    return {**basis, **abweichungen}
