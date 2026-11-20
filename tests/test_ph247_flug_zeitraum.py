"""
tests/test_ph247_flug_zeitraum.py – Phase 247 (Issue #385)

Regelbasierter Zeitraum-Parser des Flug-Bots.
"""

from datetime import date

import pytest

from bot.flug.zeitraum import ZeitraumFehler, monate, parse_zeitraum

HEUTE = date(2026, 11, 20)


def _spanne(text: str) -> tuple[date, date]:
    z = parse_zeitraum(text, HEUTE)
    return z.von, z.bis


@pytest.mark.parametrize(
    "text",
    ["März 2027", "maerz 2027", "Mär. 2027", "marzo 2027", "03/2027", "3.2027", "2027-03", "  MÄRZ   2027 "],
)
def test_ganzer_monat(text):
    assert _spanne(text) == (date(2027, 3, 1), date(2027, 3, 31))


def test_februar_endet_am_monatsletzten():
    assert _spanne("Februar 2027") == (date(2027, 2, 1), date(2027, 2, 28))


def test_monat_ohne_jahr_nimmt_naechstes_vorkommen():
    assert _spanne("März") == (date(2027, 3, 1), date(2027, 3, 31))
    assert _spanne("Dezember") == (date(2026, 12, 1), date(2026, 12, 31))


def test_laufender_monat_beginnt_heute():
    assert _spanne("November") == (HEUTE, date(2026, 11, 30))


@pytest.mark.parametrize("text", ["März - April 2027", "März bis April 2027", "März–April", "03/2027 - 04/2027"])
def test_monatsbereich(text):
    assert _spanne(text) == (date(2027, 3, 1), date(2027, 4, 30))


@pytest.mark.parametrize("text", ["Dezember - Januar", "Dez 2026 - Jan 2027", "Dezember - Januar 2027"])
def test_monatsbereich_ueber_jahreswechsel(text):
    assert _spanne(text) == (date(2026, 12, 1), date(2027, 1, 31))


@pytest.mark.parametrize(
    "text",
    ["15.03.2027 - 30.03.2027", "15.03.-30.03.2027", "15.3.–30.3.", "15.03.27-30.03.27", "15.3. bis 30.3.2027"],
)
def test_datumsbereich(text):
    assert _spanne(text) == (date(2027, 3, 15), date(2027, 3, 30))


def test_datumsbereich_ueber_jahreswechsel():
    assert _spanne("20.12.-05.01.") == (date(2026, 12, 20), date(2027, 1, 5))


def test_einzelnes_datum():
    assert _spanne("15.03.2027") == (date(2027, 3, 15), date(2027, 3, 15))


def test_maximale_laenge_ist_erlaubt():
    assert _spanne("März - Mai 2027") == (date(2027, 3, 1), date(2027, 5, 31))


@pytest.mark.parametrize(
    "text",
    ["", "irgendwann", "32.03.2027", "15.13.2027", "Mörz 2027", "13/2027", "März 2027 - April - Mai", "x" * 200],
)
def test_ungueltige_eingabe(text):
    with pytest.raises(ZeitraumFehler):
        parse_zeitraum(text, HEUTE)


def test_ende_vor_anfang():
    with pytest.raises(ZeitraumFehler, match="vor"):
        parse_zeitraum("30.03.2027 - 15.03.2027", HEUTE)


def test_vergangenheit():
    with pytest.raises(ZeitraumFehler, match="Vergangenheit"):
        parse_zeitraum("Oktober 2026", HEUTE)


def test_zu_lang():
    with pytest.raises(ZeitraumFehler, match="3 Monate"):
        parse_zeitraum("März - Juni 2027", HEUTE)


def test_zu_weit_voraus():
    with pytest.raises(ZeitraumFehler, match="12 Monate"):
        parse_zeitraum("Dezember 2027", HEUTE)


def test_monate_listet_jeden_angeschnittenen_monat():
    assert monate(date(2027, 3, 15), date(2027, 5, 2)) == ("2027-03", "2027-04", "2027-05")


def test_monate_ueber_jahreswechsel():
    assert monate(date(2026, 12, 20), date(2027, 1, 5)) == ("2026-12", "2027-01")


# ── Review M-1: Bereiche ohne Jahr, die schon begonnen haben ─────────────────


@pytest.mark.parametrize(
    "heute, text, von, bis",
    [
        (HEUTE, "Oktober bis Dezember", HEUTE, date(2026, 12, 31)),
        (HEUTE, "15.11.-30.11.", HEUTE, date(2026, 11, 30)),
        (HEUTE, "01.11.-05.12.", HEUTE, date(2026, 12, 5)),
        (HEUTE, "15.11.-20.11.", HEUTE, HEUTE),
        (HEUTE, "Oktober bis Januar", HEUTE, date(2027, 1, 31)),
        (date(2026, 12, 31), "30.12.-02.01.", date(2026, 12, 31), date(2027, 1, 2)),
        (date(2027, 1, 2), "Dezember bis Januar", date(2027, 1, 2), date(2027, 1, 31)),
        (date(2027, 1, 2), "30.12.-10.01.", date(2027, 1, 2), date(2027, 1, 10)),
    ],
)
def test_begonnener_bereich_bleibt_im_laufenden_jahr(heute, text, von, bis):
    z = parse_zeitraum(text, heute)
    assert (z.von, z.bis) == (von, bis)


# ── Live-Test 20.11.2026: Punkt nach dem Monat ist optional ──────────────────


@pytest.mark.parametrize(
    "text, von, bis",
    [
        ("01.01-31.01.2027", date(2027, 1, 1), date(2027, 1, 31)),
        ("01.01 - 31.01.2027", date(2027, 1, 1), date(2027, 1, 31)),
        ("1.1-31.1.2027", date(2027, 1, 1), date(2027, 1, 31)),
        ("15.03-30.03", date(2027, 3, 15), date(2027, 3, 30)),
        ("15.3 bis 30.3", date(2027, 3, 15), date(2027, 3, 30)),
        ("15.03", date(2027, 3, 15), date(2027, 3, 15)),
    ],
)
def test_datum_ohne_punkt_nach_dem_monat(text, von, bis):
    assert _spanne(text) == (von, bis)


@pytest.mark.parametrize("text", ["3.2027", "03.2027"])
def test_monat_mit_jahr_bleibt_monat(text):
    """Nicht mit "Tag.Monat" verwechseln: 03.2027 ist März 2027."""
    assert _spanne(text) == (date(2027, 3, 1), date(2027, 3, 31))
