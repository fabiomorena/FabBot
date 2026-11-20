"""
tests/test_ph247_flug_preise.py – Phase 247 (Issue #385)

Travelpayouts-Client (Parser + HTTP) und die Auswahl-/Alarm-Logik.
"""

from datetime import date

import httpx
import pytest

from bot.flug.auswahl import bewerte, passende_angebote
from bot.flug.travelpayouts import TravelpayoutsFehler, lade_angebote, parse_antwort
from tests.flug_testdaten import angebot, eintrag, watch

HEUTE = date(2026, 11, 20)


# ── Parser ────────────────────────────────────────────────────────────────────


def test_parse_liest_felder():
    (a,) = parse_antwort({"success": True, "currency": "eur", "data": [eintrag()]})
    assert a.preis == 89
    assert a.abflug.isoformat() == "2027-03-05T06:10:00+01:00"
    assert a.rueckflug.isoformat() == "2027-03-10T20:00:00+01:00"
    assert a.umstiege == 0
    assert a.umstiege_rueck == 1
    assert a.airline == "FR"


def test_parse_macht_relativen_link_absolut():
    (a,) = parse_antwort({"success": True, "data": [eintrag()]})
    assert a.link == "https://www.aviasales.com/search/BER0503ROM10031?t=FR1"


def test_parse_verwirft_fremde_links():
    (a,) = parse_antwort({"success": True, "data": [eintrag(link="javascript:alert(1)")]})
    assert a.link == ""


def test_parse_rundet_float_preis():
    (a,) = parse_antwort({"success": True, "data": [eintrag(price=88.6)]})
    assert a.preis == 89


def test_parse_ohne_rueckflug_ist_one_way():
    (a,) = parse_antwort({"success": True, "data": [eintrag(return_at="")]})
    assert a.rueckflug is None


def test_parse_ueberspringt_kaputte_eintraege():
    kaputt = [eintrag(price=None), eintrag(departure_at="morgen"), eintrag(price=0), "kein dict"]
    ohne_preis = {k: v for k, v in eintrag().items() if k != "price"}
    angebote = parse_antwort({"success": True, "data": [*kaputt, ohne_preis, eintrag(price=120)]})
    assert [a.preis for a in angebote] == [120]


@pytest.mark.parametrize(
    "antwort",
    [
        {"success": False, "error": "token invalid"},
        {"success": True},
        {"success": True, "data": "nope"},
        ["keine", "map"],
        {"success": True, "currency": "rub", "data": [eintrag()]},
    ],
)
def test_parse_wirft_bei_unbrauchbarer_antwort(antwort):
    with pytest.raises(TravelpayoutsFehler):
        parse_antwort(antwort)


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lade_angebote_token_nur_im_header():
    gesehen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        return httpx.Response(200, json={"success": True, "currency": "eur", "data": [eintrag()]})

    async with _client(handler) as client:
        angebote = await lade_angebote(client, "BER", "ROM", "2027-03", True, "geheim123")

    assert len(angebote) == 1
    (req,) = gesehen
    assert req.headers["x-access-token"] == "geheim123"
    assert "geheim123" not in str(req.url)
    assert req.url.params["origin"] == "BER"
    assert req.url.params["destination"] == "ROM"
    assert req.url.params["departure_at"] == "2027-03"
    assert req.url.params["one_way"] == "false"
    assert req.url.params["currency"] == "eur"


async def test_lade_angebote_one_way():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["one_way"] == "true"
        return httpx.Response(200, json={"success": True, "data": []})

    async with _client(handler) as client:
        assert await lade_angebote(client, "BER", "ROM", "2027-03", False, "t") == ()


async def test_lade_angebote_http_fehler_wirft():
    async with _client(lambda r: httpx.Response(401, text="Unauthorized")) as client:
        with pytest.raises(TravelpayoutsFehler, match="401"):
            await lade_angebote(client, "BER", "ROM", "2027-03", True, "t")


async def test_lade_angebote_kein_json_wirft():
    async with _client(lambda r: httpx.Response(200, text="<html>")) as client:
        with pytest.raises(TravelpayoutsFehler):
            await lade_angebote(client, "BER", "ROM", "2027-03", True, "t")


# ── Auswahl ───────────────────────────────────────────────────────────────────


def test_auswahl_filtert_abflug_ausserhalb_fenster():
    w = watch(von=date(2027, 3, 10), bis=date(2027, 3, 20))
    drin = angebot(abflug="2027-03-12T08:00:00+01:00", rueckflug="2027-03-15T08:00:00+01:00")
    davor = angebot(preis=10, abflug="2027-03-09T08:00:00+01:00")
    danach = angebot(preis=10, abflug="2027-03-21T08:00:00+01:00", rueckflug="2027-03-25T08:00:00+01:00")
    assert passende_angebote((davor, drin, danach), w, HEUTE) == (drin,)


def test_auswahl_filtert_naechte():
    w = watch(naechte_min=5, naechte_max=9)
    zu_kurz = angebot(preis=10, rueckflug="2027-03-07T08:00:00+01:00")
    passt = angebot(preis=50, rueckflug="2027-03-12T08:00:00+01:00")
    zu_lang = angebot(preis=10, rueckflug="2027-03-20T08:00:00+01:00")
    assert passende_angebote((zu_kurz, passt, zu_lang), w, HEUTE) == (passt,)


def test_auswahl_hin_und_zurueck_braucht_rueckflug():
    assert passende_angebote((angebot(rueckflug=None),), watch(), HEUTE) == ()


def test_auswahl_one_way_ignoriert_rundreisen():
    nur_hin = angebot(rueckflug=None)
    assert passende_angebote((angebot(preis=10), nur_hin), watch(hin_und_zurueck=False), HEUTE) == (nur_hin,)


def test_auswahl_sortiert_nach_preis():
    teuer, billig = angebot(preis=120), angebot(preis=60)
    assert passende_angebote((teuer, billig), watch(), HEUTE) == (billig, teuer)


def test_auswahl_verwirft_vergangene_abfluege():
    w = watch(von=date(2026, 11, 1), bis=date(2026, 11, 30))
    gestern = angebot(abflug="2026-11-19T08:00:00+01:00", rueckflug="2026-11-22T08:00:00+01:00")
    morgen = angebot(abflug="2026-11-21T08:00:00+01:00", rueckflug="2026-11-24T08:00:00+01:00")
    assert passende_angebote((gestern, morgen), w, HEUTE) == (morgen,)


# ── Alarm-Logik ───────────────────────────────────────────────────────────────


def test_bewerte_erster_treffer_unter_grenze_alarmiert():
    neu, alarm = bewerte(watch(), angebot(preis=89))
    assert alarm is True
    assert neu.gemeldeter_preis == 89
    assert neu.letzter_preis == 89


def test_bewerte_gleicher_preis_alarmiert_nicht_erneut():
    neu, alarm = bewerte(watch(gemeldeter_preis=89), angebot(preis=89))
    assert alarm is False
    assert neu.gemeldeter_preis == 89


def test_bewerte_niedrigerer_preis_alarmiert_erneut():
    neu, alarm = bewerte(watch(gemeldeter_preis=89), angebot(preis=79))
    assert alarm is True
    assert neu.gemeldeter_preis == 79


def test_bewerte_hoeherer_preis_unter_grenze_kein_alarm():
    neu, alarm = bewerte(watch(gemeldeter_preis=89), angebot(preis=99))
    assert alarm is False
    assert neu.gemeldeter_preis == 89
    assert neu.letzter_preis == 99


def test_bewerte_ueber_grenze_setzt_meldung_zurueck():
    neu, alarm = bewerte(watch(gemeldeter_preis=89), angebot(preis=200))
    assert alarm is False
    assert neu.gemeldeter_preis is None
    assert neu.letzter_preis == 200


def test_bewerte_ohne_angebot_behaelt_meldung():
    neu, alarm = bewerte(watch(gemeldeter_preis=89, letzter_preis=89), None)
    assert alarm is False
    assert neu.gemeldeter_preis == 89
    assert neu.letzter_preis is None


def test_bewerte_veraendert_watch_nicht():
    alt = watch()
    bewerte(alt, angebot(preis=50))
    assert alt.gemeldeter_preis is None
    assert alt.letzter_preis is None


# ── Härtung (Security-Review) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "link",
    [
        "/search/BER0503ROM10031\n\nJetzt buchen: https://evil.example/pay",
        "/search/x y",
        "//evil.example/pay",
        "/" + "a" * 1001,
        "https://evil.example/pay",
    ],
)
def test_parse_verwirft_manipulierte_links(link):
    (a,) = parse_antwort({"success": True, "data": [eintrag(link=link)]})
    assert a.link == ""


@pytest.mark.parametrize("airline", ["bit.ly/abc", "fr", "FR1", "F\nR"])
def test_parse_verwirft_ungueltige_airline(airline):
    (a,) = parse_antwort({"success": True, "data": [eintrag(airline=airline)]})
    assert a.airline == ""


def test_parse_ueberlebt_unendliche_und_riesige_werte():
    """Ein kaputter Eintrag darf nicht die ganze Antwort kosten (OverflowError)."""
    kaputt = [eintrag(price=float("inf")), eintrag(price=float("nan")), eintrag(transfers=1e400)]
    angebote = parse_antwort({"success": True, "data": [*kaputt, eintrag(price=70)]})
    assert [a.preis for a in angebote] == [70]


def test_parse_verwirft_zeiten_ohne_zeitzone():
    """Naive und aware Zeiten wären beim Sortieren nicht vergleichbar (TypeError)."""
    naiv = eintrag(departure_at="2027-03-05T06:10:00", price=50)
    angebote = parse_antwort({"success": True, "data": [naiv, eintrag(price=89)]})
    assert [a.preis for a in angebote] == [89]


def test_api_fehlertext_wird_einzeilig():
    with pytest.raises(TravelpayoutsFehler) as info:
        parse_antwort({"success": False, "error": "kaputt\nERROR gefälschte Logzeile"})
    assert "\n" not in str(info.value)


def test_parse_akzeptiert_echten_aviasales_link():
    """Live-Antwort vom 20.11.2026 (BER→ROM, hin & zurück) – 373 Zeichen."""
    echt = (
        "/search/BER0303ROM07031?t=U218040950001804102800000130BERFCO18044154001804423500000135FCOBER"
        "_ca710ec39780f2c715b087ec6017a5ec_7313&search_date=19112026"
        "&expected_price_uuid=01a1f28e-8a66-8040-aac4-6e276c9712cb"
        "&static_fare_key=TY%7CP0%7CH0%7CL0%7CCH0%7CR0%7CTBC0&itinerary_key=MTFLrV0gJtkapm7tpau43A"
        "&expected_price_source=share&expected_price_currency=eur&expected_price=81"
    )
    (a,) = parse_antwort(
        {"success": True, "currency": "eur", "data": [eintrag(link=echt, gate="Tickets", duration_to=130)]}
    )
    assert a.link == "https://www.aviasales.com" + echt
