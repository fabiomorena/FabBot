"""
tests/test_ph247_flug_integration.py – Phase 247 (Issue #385)

Der komplette Dialog durch die echte PTB-Maschinerie (Application,
Schranke in Gruppe -1, ConversationHandler, Callback-Patterns). Nur der
HTTP-Layer zu Telegram (`Bot._do_post`) und die Ortssuche sind ersetzt.

Damit ist abgesichert, was die Unit-Tests der einzelnen Callbacks nicht
zeigen können: dass Zustände, Patterns und die Schranke zusammenspielen.
"""

from datetime import date
from itertools import count

import pytest
from telegram import Update
from telegram.ext import ExtBot

from bot.flug import app as flug_app
from bot.flug import dialog, store
from bot.flug.drossel import Drossel
from bot.flug.modelle import Ort
from tests.flug_testdaten import ROM

LISSABON = Ort(code="LIS", name="Lissabon", land="Portugal")

USER = 111
FREMDER = 555
BOT_USER = {"id": 1, "is_bot": True, "first_name": "Flug", "username": "flug_test_bot"}


@pytest.fixture
async def flug(tmp_path, monkeypatch):
    """Initialisierte Flug-App ohne Netz; liefert (app, gesendete Nachrichten)."""
    monkeypatch.setattr(store, "_STORE_FILE", tmp_path / "flug_watches.json")
    monkeypatch.setattr(dialog, "_heute", lambda: date(2026, 11, 20))
    monkeypatch.setattr(dialog, "_ORTSSUCHE", Drossel(max_anzahl=30, fenster_sekunden=3600))

    async def suche(term):
        return (LISSABON,) if term.lower().startswith("lis") else (ROM,)

    monkeypatch.setattr(dialog, "suche_orte", suche)
    gesendet: list[dict] = []
    nachricht_ids = count(100)

    async def do_post(self, endpoint, data, **kwargs):
        if endpoint == "getMe":
            return BOT_USER
        if endpoint in ("sendMessage", "editMessageText"):
            gesendet.append({"endpoint": endpoint, **data})
            chat_id = data.get("chat_id", USER)
            return {
                "message_id": next(nachricht_ids),
                "date": 0,
                "chat": {"id": chat_id, "type": "private"},
                "text": data.get("text", ""),
            }
        if endpoint == "answerCallbackQuery":
            return True
        raise AssertionError(f"Unerwarteter Telegram-Aufruf: {endpoint}")

    monkeypatch.setattr(ExtBot, "_do_post", do_post)
    application = flug_app.build_flug_app("123:abc", frozenset({USER}))
    await application.initialize()
    yield application, gesendet
    await application.shutdown()


_update_ids = count(1)


def _von(user_id: int) -> dict:
    return {"id": user_id, "is_bot": False, "first_name": "Test"}


async def _schreibe(
    application, text: str, user_id: int = USER, *, chat_typ: str = "private", art: str = "message"
) -> None:
    nachricht = {
        "message_id": next(_update_ids),
        "date": 0,
        "chat": {"id": user_id, "type": chat_typ},
        "from": _von(user_id),
        "text": text,
    }
    if text.startswith("/"):
        nachricht["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if art == "edited_message":
        nachricht["edit_date"] = 1
    update = Update.de_json({"update_id": next(_update_ids), art: nachricht}, application.bot)
    await application.process_update(update)


async def _klicke(application, daten: str, user_id: int = USER) -> None:
    query = {
        "id": str(next(_update_ids)),
        "from": _von(user_id),
        "chat_instance": "x",
        "data": daten,
        "message": {
            "message_id": 50,
            "date": 0,
            "chat": {"id": user_id, "type": "private"},
            "from": BOT_USER,
            "text": "…",
        },
    }
    update = Update.de_json({"update_id": next(_update_ids), "callback_query": query}, application.bot)
    await application.process_update(update)


def _letzter_text(gesendet: list[dict]) -> str:
    return gesendet[-1]["text"]


async def test_kompletter_dialog_legt_suche_an(flug):
    application, gesendet = flug

    await _schreibe(application, "/neu")
    assert "Wohin" in _letzter_text(gesendet)
    await _schreibe(application, "Rom")
    assert "Abflug von Berlin (BER)" in _letzter_text(gesendet)
    await _klicke(application, "abflug:BER")
    assert "Wann" in _letzter_text(gesendet)
    await _schreibe(application, "März 2027")
    assert "Hin und zurück" in _letzter_text(gesendet)
    await _klicke(application, "art:rt")
    assert "Nächte" in _letzter_text(gesendet)
    await _klicke(application, "dauer:5-9")
    assert "Preis" in _letzter_text(gesendet)
    await _schreibe(application, "150")

    (w,) = store.lade_watches()
    assert (w.origin.code, w.ziel.code, w.von, w.bis) == ("BER", "ROM", date(2027, 3, 1), date(2027, 3, 31))
    assert (w.naechte_min, w.naechte_max, w.max_preis) == (5, 9, 150)
    assert any("Suche angelegt" in g["text"] for g in gesendet)

    # Dialog ist beendet: eine weitere Zahl legt nichts mehr an
    await _schreibe(application, "200")
    assert len(store.lade_watches()) == 1


async def test_fremder_erreicht_keinen_handler(flug):
    application, gesendet = flug

    await _schreibe(application, "/neu", user_id=FREMDER)
    await _schreibe(application, "Rom", user_id=FREMDER)
    await _klicke(application, "abflug:BER", user_id=FREMDER)
    await _schreibe(application, "/liste", user_id=FREMDER)

    assert store.lade_watches() == ()
    assert all(
        g["chat_id"] == FREMDER and "Kein Zugriff" in g["text"] for g in gesendet if g["endpoint"] == "sendMessage"
    )
    assert str(FREMDER) in _letzter_text(gesendet)


async def test_fremder_kann_fremde_suche_nicht_loeschen(flug):
    application, _ = flug
    await _schreibe(application, "/neu")
    await _schreibe(application, "Rom")
    await _klicke(application, "abflug:BER")
    await _schreibe(application, "März 2027")
    await _klicke(application, "art:ow")
    await _schreibe(application, "150")
    (w,) = store.lade_watches()

    await _klicke(application, f"del:{w.id}", user_id=FREMDER)
    assert len(store.lade_watches()) == 1


async def test_abbrechen_mitten_im_dialog(flug):
    application, gesendet = flug
    await _schreibe(application, "/neu")
    await _schreibe(application, "Rom")
    await _schreibe(application, "/abbrechen")
    assert _letzter_text(gesendet) == "Abgebrochen."
    # Nach dem Abbruch wird Text nicht mehr als Abflugort interpretiert
    await _schreibe(application, "Rom")
    assert "kein Dialog" in _letzter_text(gesendet)


async def test_liste_funktioniert_mitten_im_dialog(flug):
    application, gesendet = flug
    await _schreibe(application, "/neu")
    await _schreibe(application, "/liste")
    assert "keine aktiven Suchen" in _letzter_text(gesendet)
    # Dialog läuft weiter
    await _schreibe(application, "Rom")
    assert "Abflug von" in _letzter_text(gesendet)


async def test_text_statt_button_bei_art(flug):
    application, gesendet = flug
    await _schreibe(application, "/neu")
    await _schreibe(application, "Rom")
    await _klicke(application, "abflug:BER")
    await _schreibe(application, "März 2027")
    await _schreibe(application, "hin und zurück")
    assert "Buttons" in _letzter_text(gesendet)
    await _klicke(application, "art:ow")
    assert "Preis" in _letzter_text(gesendet)


async def test_bearbeitete_nachricht_zaehlt_nicht_als_eingabe(flug):
    """Review M-3: "Rom" nachträglich zu "Mailand" bearbeitet darf nicht zum Abflugort werden."""
    application, gesendet = flug
    await _schreibe(application, "/neu")
    await _schreibe(application, "Rom")
    anzahl = len(gesendet)
    await _schreibe(application, "Mailand", art="edited_message")
    assert len(gesendet) == anzahl
    # Der Dialog wartet weiter auf den Abflugort
    await _klicke(application, "abflug:BER")
    assert "Wann" in _letzter_text(gesendet)


async def test_gruppenchat_wird_abgewiesen(flug):
    """Security L8."""
    application, gesendet = flug
    await _schreibe(application, "/neu", chat_typ="group")
    assert "privat" in _letzter_text(gesendet)
    await _schreibe(application, "/liste", chat_typ="group")
    assert all("Suchen" not in g["text"] for g in gesendet)


async def test_veralteter_button_wird_quittiert(flug):
    """Review L-4: kein ewiger Spinner bei Buttons aus einem beendeten Dialog."""
    application, gesendet = flug
    await _klicke(application, "art:rt")
    assert gesendet == []


async def _lege_suche_an(application, ziel: str = "Rom") -> None:
    await _schreibe(application, "/neu")
    await _schreibe(application, ziel)
    await _klicke(application, "abflug:BER")
    await _schreibe(application, "März 2027")
    await _klicke(application, "art:ow")
    await _schreibe(application, "150")


async def test_eigene_suche_loeschen_ueber_buttons(flug):
    application, gesendet = flug
    await _lege_suche_an(application)
    (w,) = store.lade_watches()

    await _schreibe(application, "/loeschen")
    knoepfe = gesendet[-1]["reply_markup"]
    assert f"del:{w.id}" in str(knoepfe)
    await _klicke(application, f"del:{w.id}")

    assert store.lade_watches() == ()
    assert "gelöscht" in _letzter_text(gesendet)


@pytest.mark.parametrize("befehl", ["/löschen", "/Löschen", "/loeschen", "/lösche"])
async def test_loeschen_auch_mit_umlaut(flug, befehl):
    """Live-Test 20.11.2026: "/löschen" kam nie an – Telegram-Befehle kennen kein ö."""
    application, gesendet = flug
    await _lege_suche_an(application)
    await _schreibe(application, befehl)
    assert _letzter_text(gesendet).startswith("Soll diese Suche weg?")


async def test_umlaut_befehl_mitten_im_dialog_ist_keine_ortseingabe(flug, monkeypatch):
    application, gesendet = flug
    suchbegriffe: list[str] = []

    async def suche(term):
        suchbegriffe.append(term)
        return (ROM,)

    monkeypatch.setattr(dialog, "suche_orte", suche)
    await _schreibe(application, "/neu")
    await _schreibe(application, "/löschen")
    assert suchbegriffe == []
    assert _letzter_text(gesendet) == "Du hast keine aktiven Suchen."
    # Der Dialog läuft danach normal weiter
    await _schreibe(application, "Rom")
    assert suchbegriffe == ["Rom"]


# ── Live-Test 20.11.2026: Löschen per ja/nein, Nummer und Name ───────────────


def _ids() -> list[str]:
    return [w.ziel.code for w in store.lade_watches()]


async def test_eine_suche_mit_ja_loeschen(flug):
    application, gesendet = flug
    await _lege_suche_an(application)
    await _schreibe(application, "/löschen")
    assert "ja" in _letzter_text(gesendet) and "nein" in _letzter_text(gesendet)
    knoepfe = str(gesendet[-1]["reply_markup"])
    assert "Ja, löschen" in knoepfe and "del:nein" in knoepfe

    await _schreibe(application, "ja")

    assert store.lade_watches() == ()
    assert "gelöscht" in _letzter_text(gesendet)
    # Der Löschdialog ist beendet: "Rom" ist danach keine Antwort mehr
    await _schreibe(application, "Rom")
    assert "kein Dialog" in _letzter_text(gesendet)


@pytest.mark.parametrize("antwort", ["nein", "Nein!", "abbrechen"])
async def test_eine_suche_mit_nein_behalten(flug, antwort):
    application, gesendet = flug
    await _lege_suche_an(application)
    await _schreibe(application, "/loeschen")
    await _schreibe(application, antwort)
    assert _ids() == ["ROM"]
    assert "nichts gelöscht" in _letzter_text(gesendet)


async def test_nein_button_behaelt_suche(flug):
    application, gesendet = flug
    await _lege_suche_an(application)
    await _schreibe(application, "/loeschen")
    await _klicke(application, "del:nein")
    assert _ids() == ["ROM"]
    assert "nichts gelöscht" in _letzter_text(gesendet)


async def test_mehrere_suchen_per_nummer(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _lege_suche_an(application, "Lissabon")
    await _schreibe(application, "/löschen")
    text = _letzter_text(gesendet)
    assert "1. Berlin (BER) → Rom (ROM)" in text and "2. Berlin (BER) → Lissabon (LIS)" in text

    await _schreibe(application, "2")

    assert _ids() == ["ROM"]


async def test_mehrere_suchen_ja_fragt_nach_nummer(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _lege_suche_an(application, "Lissabon")
    await _schreibe(application, "/löschen")
    await _schreibe(application, "ja")
    assert _ids() == ["ROM", "LIS"]
    assert "Nummer" in _letzter_text(gesendet)
    await _schreibe(application, "1")
    assert _ids() == ["LIS"]


async def test_mehrere_suchen_per_name(flug):
    application, _ = flug
    await _lege_suche_an(application, "Rom")
    await _lege_suche_an(application, "Lissabon")
    await _schreibe(application, "/loeschen")
    await _schreibe(application, "lissabon")
    assert _ids() == ["ROM"]


async def test_loeschen_mit_name_direkt(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _lege_suche_an(application, "Lissabon")
    await _schreibe(application, "/löschen Lissabon")
    assert _ids() == ["ROM"]
    assert "gelöscht" in _letzter_text(gesendet)


async def test_loeschen_mit_unbekanntem_namen_zeigt_liste(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _schreibe(application, "/loeschen Paris")
    assert any("Keine Suche passt" in g["text"] for g in gesendet)
    assert _letzter_text(gesendet).startswith("Soll diese Suche weg?")
    assert _ids() == ["ROM"]


async def test_unverstaendliche_antwort_bleibt_im_loeschdialog(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _lege_suche_an(application, "Lissabon")
    await _schreibe(application, "/loeschen")
    await _schreibe(application, "die eine da")
    assert "nicht verstanden" in _letzter_text(gesendet)
    await _schreibe(application, "1")
    assert _ids() == ["LIS"]


async def test_loeschen_mitten_in_neuer_suche(flug):
    application, gesendet = flug
    await _lege_suche_an(application, "Rom")
    await _schreibe(application, "/neu")
    await _schreibe(application, "/löschen")
    assert _letzter_text(gesendet).startswith("Soll diese Suche weg?")
    await _schreibe(application, "ja")
    assert _ids() == []
