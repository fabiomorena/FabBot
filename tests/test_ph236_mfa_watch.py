"""
tests/test_ph236_mfa_watch.py – Phase 236 (Issue #343)

MFA-Ausbildungsplatz-Watcher: Parser, Paginierung, Dedup, Fehlerpfad, Scheduler.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot import mfa_watch

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def state_isoliert(tmp_path, monkeypatch):
    """Verhindert, dass Tests in die echte ~/.fabbot/mfa_watch_state.json schreiben."""
    monkeypatch.setattr(mfa_watch, "_STATE_FILE", tmp_path / "mfa_watch_state.json")
    yield


def _mock_client(*antworten: str) -> MagicMock:
    """httpx.AsyncClient-Mock, der die übergebenen HTML-Seiten der Reihe nach liefert."""
    client = MagicMock()
    responses = []
    for html in antworten:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        responses.append(resp)
    client.get = AsyncMock(side_effect=responses)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


# ── Parser ────────────────────────────────────────────────────────────────────


def test_parser_findet_alle_items():
    anzeigen = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))
    assert len(anzeigen) == 2


def test_parser_liest_alle_felder():
    erste = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[0]
    assert erste["uid"] == "3339"
    assert erste["praxis"] == "MVZ am Bahnhof Spandau"
    assert erste["adresse"] == "Galenstraße 3, 13597 Berlin"
    assert erste["bezirk"] == "Spandau"
    assert erste["email"] == "plueckhahn@mvz-bahnhof-spandau.de"
    assert erste["beginn"] == "01.02.2027"
    assert erste["veroeffentlicht"] == "29.09.2026"
    assert "Innere Medizin" in erste["fachrichtungen"]


def test_parser_vertraegt_fehlende_felder():
    """Zweites Item hat keinen Ausbildungsbeginn – darf nicht knallen."""
    zweite = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[1]
    assert zweite["uid"] == "3335"
    assert zweite["praxis"] == "Gemeinschaftspraxis Höpner und Besson"
    assert zweite["beginn"] == ""


def test_parser_liest_telefon_und_webseite():
    """Nicht jede Praxis hinterlegt eine E-Mail – dann zählen Telefon und Internetseite."""
    anzeige = mfa_watch._parse_anzeigen(_fixture("mfa_telefon.html"))[0]
    assert anzeige["email"] == ""
    assert anzeige["telefon"]
    assert anzeige["webseite"]
    assert "Post" in anzeige["versand"] or "E-Mail" in anzeige["versand"]


def test_formatiere_faellt_auf_telefon_zurueck():
    anzeige = mfa_watch._parse_anzeigen(_fixture("mfa_telefon.html"))[0]
    text = mfa_watch._formatiere(anzeige, "https://example.org")
    assert "Telefon:" in text
    assert "Kontakt:" not in text
    assert "Bewerbung:" in text, "Ohne E-Mail muss der Bewerbungsweg dastehen"


def test_formatiere_zeigt_bei_email_keinen_bewerbungsweg():
    erste = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[0]
    text = mfa_watch._formatiere(erste, "https://example.org")
    assert "Kontakt:" in text
    assert "Telefon:" not in text


def test_parser_leere_seite():
    assert mfa_watch._parse_anzeigen(_fixture("mfa_leer.html")) == []


def test_gesamttreffer_aus_result_info():
    assert mfa_watch._gesamttreffer(_fixture("mfa_seite1.html")) == 3
    assert mfa_watch._gesamttreffer(_fixture("mfa_leer.html")) == 0
    assert mfa_watch._gesamttreffer("<html></html>") is None


def test_gesamttreffer_vertraegt_tags_zwischen_label_und_zahl():
    """Live-Markup lautet 'Ergebnisse: <b>20</b>' – ohne Tag-Toleranz greift die Regex nicht."""
    echt = '<div class="vd-result-info vd-result-list--board"><p>Ergebnisse: <b>20</b></p></div>'
    assert mfa_watch._gesamttreffer(echt) == 20


async def test_lade_anzeigen_stoppt_wenn_trefferzahl_erreicht():
    """Die Börse klemmt Seitenüberläufe auf die letzte Seite – ohne Abbruch entstehen Leerabrufe."""
    ctx, client = _mock_client(_fixture("mfa_seite1.html"), _fixture("mfa_seite2.html"), _fixture("mfa_seite2.html"))
    with patch("httpx.AsyncClient", return_value=ctx):
        anzeigen = await mfa_watch.lade_anzeigen("https://example.org/boerse")
    assert len(anzeigen) == 3
    assert client.get.call_count == 2, "dritter Abruf ist überflüssig, sobald die Trefferzahl erreicht ist"


def test_anzeige_id_nutzt_uid():
    erste = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[0]
    assert mfa_watch._anzeige_id(erste) == "uid:3339"


def test_anzeige_id_faellt_auf_hash_zurueck():
    ohne_uid = {"uid": "", "praxis": " Praxis  Test ", "adresse": "Weg 1", "veroeffentlicht": "01.01.2026"}
    eins = mfa_watch._anzeige_id(ohne_uid)
    zwei = mfa_watch._anzeige_id({**ohne_uid, "praxis": "praxis test"})
    assert eins.startswith("sha:")
    assert eins == zwei, "Normalisierung muss Schreibweise und Leerraum angleichen"


# ── Formatierung ──────────────────────────────────────────────────────────────


def test_formatiere_enthaelt_kernfelder():
    erste = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[0]
    text = mfa_watch._formatiere(erste, "https://example.org/boerse")
    assert "MVZ am Bahnhof Spandau" in text
    assert "Spandau" in text
    assert "plueckhahn@mvz-bahnhof-spandau.de" in text
    assert "29.09.2026" in text
    assert "https://example.org/boerse" in text


def test_formatiere_laesst_leere_felder_weg():
    zweite = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[1]
    text = mfa_watch._formatiere(zweite, "https://example.org/boerse")
    assert "Beginn:" not in text


def test_formatiere_escaped_markdown():
    anzeige = {
        "uid": "1",
        "praxis": "Dr. Schwarz & Dr. Heibült_Praxis",
        "adresse": "Bundesallee 104",
        "bezirk": "Mitte",
        "fachrichtungen": "Innere Medizin",
        "beginn": "ab sofort",
        "email": "a@b.de",
        "veroeffentlicht": "01.01.2026",
    }
    text = mfa_watch._formatiere(anzeige, "https://example.org")
    assert "\\_" in text, "Unterstrich muss escaped sein, sonst bricht Telegram-Markdown"


# ── Abruf und Paginierung ─────────────────────────────────────────────────────


async def test_lade_anzeigen_folgt_paginierung():
    ctx, client = _mock_client(_fixture("mfa_seite1.html"), _fixture("mfa_seite2.html"))
    with patch("httpx.AsyncClient", return_value=ctx):
        anzeigen = await mfa_watch.lade_anzeigen("https://example.org/boerse")
    assert [a["uid"] for a in anzeigen] == ["3339", "3335", "3338"]
    assert client.get.call_count == 2
    zweite_url = client.get.call_args_list[1].args[0]
    assert "currentPage" in zweite_url


async def test_lade_anzeigen_stoppt_ohne_weitere_seite():
    ctx, client = _mock_client(_fixture("mfa_leer.html"))
    with patch("httpx.AsyncClient", return_value=ctx):
        anzeigen = await mfa_watch.lade_anzeigen("https://example.org/boerse")
    assert anzeigen == []
    assert client.get.call_count == 1


async def test_lade_anzeigen_wirft_bei_fehlerstatus():
    resp = MagicMock()
    resp.status_code = 403
    resp.text = ""
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=ctx):
        with pytest.raises(mfa_watch.MfaAbrufFehler):
            await mfa_watch.lade_anzeigen("https://example.org/boerse")


# ── pruefe_einmal ─────────────────────────────────────────────────────────────


def _anzeigen(*uids: str) -> list[dict]:
    return [
        {
            "uid": u,
            "praxis": f"Praxis {u}",
            "adresse": "Teststraße 1 10000 Berlin",
            "bezirk": "Mitte",
            "fachrichtungen": "Allgemeinmedizin",
            "beginn": "ab sofort",
            "email": f"{u}@example.org",
            "veroeffentlicht": "01.09.2026",
        }
        for u in uids
    ]


async def test_erster_lauf_sendet_nichts():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1", "2"))):
        await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    bot.send_message.assert_not_called()
    state = json.loads(mfa_watch._STATE_FILE.read_text(encoding="utf-8"))
    assert set(state["seen"]) == {"uid:1", "uid:2"}


async def test_adresse_mit_komma_zwischen_strasse_und_ort():
    """Strasse und PLZ stehen im Markup als getrennte Segmente – ohne Komma liest es sich schlecht."""
    erste = mfa_watch._parse_anzeigen(_fixture("mfa_seite1.html"))[0]
    assert erste["adresse"] == "Galenstraße 3, 13597 Berlin"


async def test_sendet_ohne_link_vorschau():
    """Die Vorschaukarte zeigt bei jeder Anzeige dieselbe generische Seitenbeschreibung."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1"))):
        await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1", "2"))):
        with patch("asyncio.sleep", AsyncMock()):
            await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    vorschau = bot.send_message.call_args.kwargs["link_preview_options"]
    assert vorschau.is_disabled is True


async def test_neue_anzeige_wird_gesendet():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1"))):
        await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1", "2"))):
        with patch("asyncio.sleep", AsyncMock()):
            await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    assert bot.send_message.call_count == 1
    assert bot.send_message.call_args.kwargs["chat_id"] == "-100123"
    assert "Praxis 2" in bot.send_message.call_args.kwargs["text"]


async def test_unveraenderte_seite_sendet_nichts():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    for _ in range(2):
        with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1", "2"))):
            await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    bot.send_message.assert_not_called()


async def test_null_items_gilt_als_fehlschlag():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1"))):
        await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=[])):
        with pytest.raises(mfa_watch.MfaAbrufFehler):
            await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    bot.send_message.assert_not_called()


async def test_state_bleibt_bei_sendefehler_unveraendert():
    """Eine Anzeige, die nicht rausging, darf beim nächsten Lauf nicht als gesehen gelten."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1"))):
        await mfa_watch.pruefe_einmal(bot, "-100123", 42)

    bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram down"))
    with patch.object(mfa_watch, "lade_anzeigen", AsyncMock(return_value=_anzeigen("1", "2"))):
        with pytest.raises(RuntimeError):
            await mfa_watch.pruefe_einmal(bot, "-100123", 42)
    state = json.loads(mfa_watch._STATE_FILE.read_text(encoding="utf-8"))
    assert "uid:2" not in state["seen"]


# ── Scheduler-Schleife und Fehlerschwelle ─────────────────────────────────────


async def _eine_iteration(coro_fn, *args) -> None:
    with patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
        with pytest.raises(asyncio.CancelledError):
            await coro_fn(*args)


async def test_scheduler_ruft_pruefung_auf():
    bot = MagicMock()
    pruefung = AsyncMock()
    with patch.object(mfa_watch, "pruefe_einmal", pruefung):
        await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)
    pruefung.assert_awaited_once()


async def test_fehler_meldet_erst_ab_schwelle_und_nur_an_privatchat():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    kaputt = AsyncMock(side_effect=mfa_watch.MfaAbrufFehler("403"))

    with patch.object(mfa_watch, "pruefe_einmal", kaputt):
        for _ in range(mfa_watch._FEHLER_SCHWELLE):
            await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)

    assert bot.send_message.call_count == 1
    assert bot.send_message.call_args.kwargs["chat_id"] == 42

    # Vierter Fehlschlag meldet nicht erneut
    with patch.object(mfa_watch, "pruefe_einmal", kaputt):
        await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)
    assert bot.send_message.call_count == 1


async def test_erfolg_setzt_fehlerzaehler_zurueck():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    kaputt = AsyncMock(side_effect=mfa_watch.MfaAbrufFehler("403"))

    with patch.object(mfa_watch, "pruefe_einmal", kaputt):
        for _ in range(mfa_watch._FEHLER_SCHWELLE - 1):
            await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)
    with patch.object(mfa_watch, "pruefe_einmal", AsyncMock()):
        await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)
    with patch.object(mfa_watch, "pruefe_einmal", kaputt):
        for _ in range(mfa_watch._FEHLER_SCHWELLE - 1):
            await _eine_iteration(mfa_watch.run_mfa_watch_scheduler, bot, "-100123", 42)

    bot.send_message.assert_not_called()
