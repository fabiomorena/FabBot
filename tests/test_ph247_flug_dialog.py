"""
tests/test_ph247_flug_dialog.py – Phase 247 (Issue #385)

Telegram-Dialog des Flug-Bots: /neu → Ziel → Abflug → Zeitraum → Art →
Dauer → Preisgrenze, dazu /liste und /loeschen.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from telegram.ext import ConversationHandler

from bot.flug import dialog, loeschen, store
from bot.flug.drossel import Drossel
from bot.flug.orte import OrtsFehler
from tests.flug_testdaten import BERLIN, ROM, angebot, watch

PARIS = dialog.Ort(code="PAR", name="Paris", land="Frankreich")


@pytest.fixture(autouse=True)
def isoliert(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE_FILE", tmp_path / "flug_watches.json")
    monkeypatch.setattr(dialog, "get_settings", lambda: _settings())
    monkeypatch.setattr(dialog, "_heute", lambda: date(2026, 11, 20))
    monkeypatch.setattr(dialog, "_ORTSSUCHE", Drossel(max_anzahl=30, fenster_sekunden=3600))
    monkeypatch.setattr(dialog, "_SOFORTPRUEFUNG", Drossel(max_anzahl=5, fenster_sekunden=3600))
    yield


def _settings(**abweichungen) -> SimpleNamespace:
    basis = {"travelpayouts_token": SecretStr("")}
    return SimpleNamespace(**{**basis, **abweichungen})


def _text(text: str, user_id: int = 111) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.effective_message.text = text
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def _klick(daten: str, user_id: int = 111) -> MagicMock:
    update = _text("", user_id)
    update.callback_query = MagicMock()
    update.callback_query.data = daten
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _ctx(**entwurf) -> SimpleNamespace:
    return SimpleNamespace(user_data={"flug": entwurf} if entwurf else {})


def _antwort(update: MagicMock) -> str:
    return update.effective_message.reply_text.await_args.args[0]


def _buttons(update: MagicMock) -> list[str]:
    markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
    return [knopf.callback_data for zeile in markup.inline_keyboard for knopf in zeile]


# ── Start ─────────────────────────────────────────────────────────────────────


async def test_neu_setzt_entwurf_zurueck():
    ctx = _ctx(ziel=ROM)
    assert await dialog.cmd_neu(_text("/neu"), ctx) == dialog.ZIEL
    assert ctx.user_data["flug"] == {}


# ── Ziel ──────────────────────────────────────────────────────────────────────


async def test_ziel_eindeutig_springt_zu_abflug(monkeypatch):
    monkeypatch.setattr(dialog, "suche_orte", AsyncMock(return_value=(ROM,)))
    ctx, update = _ctx(), _text("Rom")
    assert await dialog.on_ziel(update, ctx) == dialog.ABFLUG
    assert ctx.user_data["flug"]["ziel"] == ROM
    assert _buttons(update) == ["abflug:BER"]


async def test_ziel_mehrdeutig_zeigt_auswahl(monkeypatch):
    monkeypatch.setattr(dialog, "suche_orte", AsyncMock(return_value=(ROM, PARIS)))
    ctx, update = _ctx(), _text("Ro")
    assert await dialog.on_ziel(update, ctx) == dialog.ZIEL
    assert _buttons(update) == ["ziel:ROM", "ziel:PAR"]


async def test_ziel_zu_kurz_fragt_nicht_die_api(monkeypatch):
    suche = AsyncMock()
    monkeypatch.setattr(dialog, "suche_orte", suche)
    assert await dialog.on_ziel(_text("R"), _ctx()) == dialog.ZIEL
    suche.assert_not_awaited()


async def test_ziel_auswahl_per_klick(monkeypatch):
    ctx = _ctx(optionen={"ROM": ROM, "PAR": PARIS})
    update = _klick("ziel:PAR")
    assert await dialog.on_ort_klick(update, ctx) == dialog.ABFLUG
    assert ctx.user_data["flug"]["ziel"] == PARIS


async def test_ziel_klick_auf_unbekannten_code_wird_ignoriert():
    ctx = _ctx(optionen={"ROM": ROM})
    assert await dialog.on_ort_klick(_klick("ziel:XXX"), ctx) == dialog.ZIEL
    assert "ziel" not in ctx.user_data["flug"]


async def test_ziel_nichts_gefunden(monkeypatch):
    monkeypatch.setattr(dialog, "suche_orte", AsyncMock(return_value=()))
    update = _text("Atlantis")
    assert await dialog.on_ziel(update, _ctx()) == dialog.ZIEL
    assert "Nichts gefunden" in _antwort(update)


async def test_ziel_suche_nicht_erreichbar(monkeypatch):
    monkeypatch.setattr(dialog, "suche_orte", AsyncMock(side_effect=OrtsFehler("HTTP 503")))
    update = _text("Rom")
    assert await dialog.on_ziel(update, _ctx()) == dialog.ZIEL
    assert "nicht erreichbar" in _antwort(update)


# ── Abflug ────────────────────────────────────────────────────────────────────


async def test_abflug_standard_per_klick():
    ctx = _ctx(ziel=ROM)
    assert await dialog.on_ort_klick(_klick("abflug:BER"), ctx) == dialog.ZEITRAUM
    assert ctx.user_data["flug"]["origin"] == BERLIN.model_copy(update={"land": ""})


async def test_abflug_gleich_ziel_wird_abgelehnt(monkeypatch):
    monkeypatch.setattr(dialog, "suche_orte", AsyncMock(return_value=(ROM,)))
    ctx, update = _ctx(ziel=ROM), _text("Rom")
    assert await dialog.on_abflug(update, ctx) == dialog.ABFLUG
    assert "origin" not in ctx.user_data["flug"]


# ── Zeitraum, Art, Dauer ──────────────────────────────────────────────────────


async def test_zeitraum_ungueltig_bleibt_im_schritt():
    update = _text("irgendwann")
    assert await dialog.on_zeitraum(update, _ctx(ziel=ROM, origin=BERLIN)) == dialog.ZEITRAUM
    assert "März 2027" in _antwort(update)


async def test_zeitraum_gueltig():
    ctx = _ctx(ziel=ROM, origin=BERLIN)
    update = _text("März 2027")
    assert await dialog.on_zeitraum(update, ctx) == dialog.ART
    assert (ctx.user_data["flug"]["von"], ctx.user_data["flug"]["bis"]) == (date(2027, 3, 1), date(2027, 3, 31))
    assert _buttons(update) == ["art:rt", "art:ow"]


async def test_art_nur_hin_ueberspringt_dauer():
    ctx = _ctx(ziel=ROM)
    assert await dialog.on_art(_klick("art:ow"), ctx) == dialog.PREIS
    assert ctx.user_data["flug"]["hin_und_zurueck"] is False


async def test_art_hin_und_zurueck_fragt_dauer():
    ctx = _ctx(ziel=ROM)
    assert await dialog.on_art(_klick("art:rt"), ctx) == dialog.DAUER
    assert ctx.user_data["flug"]["hin_und_zurueck"] is True


@pytest.mark.parametrize("daten, erwartet", [("dauer:5-9", (5, 9)), ("dauer:egal", (None, None))])
async def test_dauer_per_klick(daten, erwartet):
    ctx = _ctx(ziel=ROM)
    assert await dialog.on_dauer_klick(_klick(daten), ctx) == dialog.PREIS
    assert ctx.user_data["flug"]["naechte"] == erwartet


@pytest.mark.parametrize("text, erwartet", [("3-7", (3, 7)), ("3 – 7", (3, 7)), ("7", (7, 7))])
async def test_dauer_per_text(text, erwartet):
    ctx = _ctx(ziel=ROM)
    assert await dialog.on_dauer_text(_text(text), ctx) == dialog.PREIS
    assert ctx.user_data["flug"]["naechte"] == erwartet


@pytest.mark.parametrize("text", ["9-3", "viele", "0-99"])
async def test_dauer_ungueltig(text):
    assert await dialog.on_dauer_text(_text(text), _ctx(ziel=ROM)) == dialog.DAUER


# ── Preis + Speichern ─────────────────────────────────────────────────────────


def _fertiger_entwurf() -> dict:
    return {
        "ziel": ROM,
        "origin": BERLIN,
        "von": date(2027, 3, 1),
        "bis": date(2027, 3, 31),
        "hin_und_zurueck": True,
        "naechte": (5, 9),
    }


@pytest.mark.parametrize("text", ["abc", "5", "99999", "-10"])
async def test_preis_ungueltig(text):
    assert await dialog.on_preis(_text(text), _ctx(**_fertiger_entwurf())) == dialog.PREIS
    assert store.lade_watches() == ()


@pytest.mark.parametrize("text", ["150", "150 €", "150€", "150,00", "150 Euro"])
async def test_preis_speichert_watch(text):
    ctx = _ctx(**_fertiger_entwurf())
    update = _text(text)
    assert await dialog.on_preis(update, ctx) == ConversationHandler.END
    (w,) = store.lade_watches()
    assert (w.user_id, w.chat_id, w.max_preis) == (111, 111, 150)
    assert (w.naechte_min, w.naechte_max) == (5, 9)
    assert w.ziel == ROM
    assert ctx.user_data["flug"] == {}
    assert "noch nicht eingerichtet" in _antwort(update)


async def test_preis_mit_token_prueft_sofort(monkeypatch):
    monkeypatch.setattr(dialog, "get_settings", lambda: _settings(travelpayouts_token=SecretStr("tp")))

    async def erste_pruefung(w, token):
        assert token == "tp"
        return w.model_copy(update={"letzter_preis": 89, "gemeldeter_preis": 89}), angebot(preis=89), True

    monkeypatch.setattr(dialog, "erste_pruefung", erste_pruefung)
    update = _text("150")
    assert await dialog.on_preis(update, _ctx(**_fertiger_entwurf())) == ConversationHandler.END
    texte = [c.args[0] for c in update.effective_message.reply_text.await_args_list]
    assert any("89 €" in t for t in texte)
    assert store.lade_watches()[0].gemeldeter_preis == 89


async def test_sofortalarm_nicht_zugestellt_gilt_nicht_als_gemeldet(monkeypatch):
    """Review HIGH-2: sonst alarmiert der Watcher erst wieder unter diesem Preis."""
    monkeypatch.setattr(dialog, "get_settings", lambda: _settings(travelpayouts_token=SecretStr("tp")))

    async def erste_pruefung(w, token):
        return w.model_copy(update={"letzter_preis": 89, "gemeldeter_preis": 89}), angebot(preis=89), True

    monkeypatch.setattr(dialog, "erste_pruefung", erste_pruefung)
    update = _text("150")

    async def antwort(text, **kwargs):
        if "Preisalarm" in text:
            raise RuntimeError("TimedOut")

    update.effective_message.reply_text = AsyncMock(side_effect=antwort)
    assert await dialog.on_preis(update, _ctx(**_fertiger_entwurf())) == ConversationHandler.END
    (w,) = store.lade_watches()
    assert (w.gemeldeter_preis, w.letzter_preis) == (None, 89)


async def test_sofortpruefung_ist_gedrosselt(monkeypatch):
    """Security M3: Anlegen/Löschen-Schleifen dürfen den Travelpayouts-Account nicht fluten."""
    monkeypatch.setattr(dialog, "get_settings", lambda: _settings(travelpayouts_token=SecretStr("tp")))
    monkeypatch.setattr(dialog, "_SOFORTPRUEFUNG", Drossel(max_anzahl=1, fenster_sekunden=3600))
    pruefung = AsyncMock(side_effect=lambda w, token: (w, None, False))
    monkeypatch.setattr(dialog, "erste_pruefung", pruefung)

    for _ in range(3):
        await dialog.on_preis(_text("150"), _ctx(**_fertiger_entwurf()))

    assert pruefung.await_count == 1


async def test_ortssuche_ist_gedrosselt(monkeypatch):
    suche = AsyncMock(return_value=(ROM,))
    monkeypatch.setattr(dialog, "suche_orte", suche)
    monkeypatch.setattr(dialog, "_ORTSSUCHE", Drossel(max_anzahl=2, fenster_sekunden=3600))
    for _ in range(4):
        await dialog.on_ziel(_text("Rom"), _ctx())
    assert suche.await_count == 2


async def test_preis_sofortpruefung_fehlschlag_ist_kein_abbruch(monkeypatch):
    monkeypatch.setattr(dialog, "get_settings", lambda: _settings(travelpayouts_token=SecretStr("tp")))
    monkeypatch.setattr(dialog, "erste_pruefung", AsyncMock(side_effect=RuntimeError("HTTP 500")))
    update = _text("150")
    assert await dialog.on_preis(update, _ctx(**_fertiger_entwurf())) == ConversationHandler.END
    assert len(store.lade_watches()) == 1
    assert "später" in _antwort(update)


async def test_preis_limit_erreicht(monkeypatch):
    monkeypatch.setattr(store, "MAX_WATCHES_PRO_USER", 0)
    update = _text("150")
    assert await dialog.on_preis(update, _ctx(**_fertiger_entwurf())) == ConversationHandler.END
    assert "maximal" in _antwort(update)


async def test_preis_ohne_entwurf_beendet_sauber():
    update = _text("150")
    assert await dialog.on_preis(update, _ctx()) == ConversationHandler.END
    assert store.lade_watches() == ()


async def test_abbrechen():
    ctx = _ctx(ziel=ROM)
    assert await dialog.cmd_abbrechen(_text("/abbrechen"), ctx) == ConversationHandler.END
    assert ctx.user_data["flug"] == {}


# ── Liste + Löschen ───────────────────────────────────────────────────────────


async def test_liste_zeigt_nur_eigene():
    await store.fuege_hinzu(watch(id="0000000a"))
    await store.fuege_hinzu(watch(id="0000000b", user_id=222, chat_id=222, ziel=PARIS))
    update = _text("/liste")
    await dialog.cmd_liste(update, _ctx())
    assert "Rom" in _antwort(update)
    assert "Paris" not in _antwort(update)


async def test_liste_leer():
    update = _text("/liste")
    await dialog.cmd_liste(update, _ctx())
    assert "/neu" in _antwort(update)


async def test_loeschen_zeigt_nur_eigene_buttons():
    await store.fuege_hinzu(watch(id="0000000a"))
    await store.fuege_hinzu(watch(id="0000000b", user_id=222, chat_id=222))
    update = _text("/loeschen")
    assert await loeschen.cmd_loeschen(update, _ctx()) == loeschen.LOESCHEN
    assert _buttons(update) == ["del:0000000a", "del:nein"]


async def test_loeschen_fremder_suche_per_klick_scheitert():
    await store.fuege_hinzu(watch(id="0000000b", user_id=222, chat_id=222))
    update = _klick("del:0000000b", user_id=111)
    await loeschen.on_loeschen_klick(update, _ctx())
    assert len(store.lade_watches()) == 1


async def test_loeschen_eigener_suche():
    await store.fuege_hinzu(watch(id="0000000a"))
    update = _klick("del:0000000a")
    assert await loeschen.on_loeschen_klick(update, _ctx()) == ConversationHandler.END
    assert store.lade_watches() == ()
    assert "gelöscht" in update.callback_query.edit_message_text.await_args.args[0]


def test_handler_liste_vollstaendig():
    handler = dialog.build_handlers()
    assert any(isinstance(h, ConversationHandler) for h in handler)


async def test_unbekannte_eingabe_bekommt_hinweis():
    """Review L-4: z. B. nach einem Neustart mitten im Dialog – keine stille Lücke."""
    update = _text("150")
    await dialog.on_unbekannt(update, _ctx())
    assert "/neu" in _antwort(update)


async def test_veralteter_button_wird_quittiert():
    update = _klick("art:rt")
    await dialog.on_veralteter_klick(update, _ctx())
    update.callback_query.answer.assert_awaited_once()


async def test_loeschen_ohne_suchen_aendert_keinen_zustand():
    update = _text("/loeschen")
    assert await loeschen.cmd_loeschen(update, _ctx()) is None
    assert "keine aktiven Suchen" in _antwort(update)


@pytest.mark.parametrize(
    "eingabe, erwartet",
    [
        ("ja", ()),
        ("1", ("0000000a",)),
        ("2", ("0000000b",)),
        ("3", ()),
        ("0", ()),
        ("rom", ("0000000a",)),
        ("PAR", ("0000000b",)),
        ("Pa", ("0000000b",)),
        ("", ()),
    ],
)
def test_finde(eingabe, erwartet):
    watches = (watch(id="0000000a"), watch(id="0000000b", ziel=PARIS))
    assert tuple(w.id for w in loeschen.finde(eingabe, watches)) == erwartet


def test_finde_ja_bei_genau_einer_suche():
    (w,) = loeschen.finde("Ja!", (watch(),))
    assert w.id == "ab12cd34"
