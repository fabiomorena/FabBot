"""
tests/test_ph247_flug_watcher.py – Phase 247 (Issue #385)

Watcher-Lauf (Alarm, Ablauf, Fehlerpfad, Scheduler) und Texte.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.flug import store, watcher
from bot.flug.texte import alert_text, watch_text, zeitraum_text
from bot.flug.travelpayouts import TravelpayoutsFehler
from tests.flug_testdaten import angebot, watch

HEUTE = date(2026, 11, 20)
JETZT = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
ERLAUBT = frozenset({111, 222})


@pytest.fixture(autouse=True)
def isoliert(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE_FILE", tmp_path / "flug_watches.json")
    monkeypatch.setattr(watcher, "_PAUSE_SEKUNDEN", 0)
    yield


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── pruefe_watch ──────────────────────────────────────────────────────────────


async def test_pruefe_watch_fragt_jeden_monat_ab(monkeypatch):
    lade = AsyncMock(
        side_effect=[
            (angebot(preis=120),),
            (angebot(preis=60, abflug="2027-04-02T08:00:00+02:00", rueckflug="2027-04-06T08:00:00+02:00"),),
        ]
    )
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    w = watch(von=date(2027, 3, 20), bis=date(2027, 4, 10))
    neu, bestes, alarm = await watcher.pruefe_watch(MagicMock(), w, "t", HEUTE)
    assert [c.args[3] for c in lade.await_args_list] == ["2027-03", "2027-04"]
    assert bestes.preis == 60
    assert alarm is True
    assert neu.gemeldeter_preis == 60


# ── pruefe_alle ───────────────────────────────────────────────────────────────


async def test_alarm_wird_genau_einmal_gesendet(monkeypatch):
    monkeypatch.setattr(watcher, "lade_angebote", AsyncMock(return_value=(angebot(preis=89),)))
    await store.fuege_hinzu(watch())
    bot = _bot()

    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)
    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    assert bot.send_message.await_count == 1
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 111
    assert "89 €" in kwargs["text"]
    assert "parse_mode" not in kwargs
    assert store.lade_watches()[0].gemeldeter_preis == 89


async def test_fehlgeschlagener_versand_merkt_preis_nicht(monkeypatch):
    monkeypatch.setattr(watcher, "lade_angebote", AsyncMock(return_value=(angebot(preis=89),)))
    await store.fuege_hinzu(watch())
    bot = _bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram weg"))

    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    gespeichert = store.lade_watches()[0]
    assert gespeichert.gemeldeter_preis is None
    assert gespeichert.letzter_preis == 89


async def test_abgelaufene_suche_wird_entfernt_und_gemeldet(monkeypatch):
    lade = AsyncMock(return_value=())
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    await store.fuege_hinzu(watch(von=date(2026, 11, 1), bis=date(2026, 11, 19)))
    bot = _bot()

    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    assert store.lade_watches() == ()
    lade.assert_not_awaited()
    assert "abgelaufen" in bot.send_message.await_args.kwargs["text"]


async def test_teilausfall_wirft_nicht(monkeypatch):
    lade = AsyncMock(side_effect=[TravelpayoutsFehler("HTTP 500"), (angebot(preis=89),)])
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    await store.fuege_hinzu(watch(id="0000000a"))
    await store.fuege_hinzu(watch(id="0000000b"))

    await watcher.pruefe_alle(_bot(), "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    preise = {w.id: w.letzter_preis for w in store.lade_watches()}
    assert preise == {"0000000a": None, "0000000b": 89}


async def test_totalausfall_wirft(monkeypatch):
    monkeypatch.setattr(watcher, "lade_angebote", AsyncMock(side_effect=TravelpayoutsFehler("HTTP 401")))
    await store.fuege_hinzu(watch())
    with pytest.raises(TravelpayoutsFehler):
        await watcher.pruefe_alle(_bot(), "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)


async def test_ohne_watches_kein_abruf(monkeypatch):
    lade = AsyncMock()
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    await watcher.pruefe_alle(_bot(), "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)
    lade.assert_not_awaited()


async def test_nicht_mehr_freigeschaltete_werden_uebersprungen_nicht_geloescht(monkeypatch):
    """Security M2: Rechte-Entzug wirkt – ohne Daten zu vernichten."""
    lade = AsyncMock(return_value=(angebot(preis=89),))
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    await store.fuege_hinzu(watch(id="0000000c", user_id=333, chat_id=333))
    bot = _bot()

    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    lade.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert len(store.lade_watches()) == 1


async def test_frisch_angelegte_suche_wird_ausgelassen(monkeypatch):
    """Review L-2: sonst könnten Sofortprüfung und Watcher beide alarmieren."""
    lade = AsyncMock(return_value=(angebot(preis=89),))
    monkeypatch.setattr(watcher, "lade_angebote", lade)
    await store.fuege_hinzu(watch(angelegt=JETZT - timedelta(minutes=5)))

    await watcher.pruefe_alle(_bot(), "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    lade.assert_not_awaited()


async def test_kein_alarm_fuer_waehrend_der_pruefung_geloeschte_suche(monkeypatch):
    """Review L-1."""

    async def lade_und_loesche(*args, **kwargs):
        await store.entferne(111, "ab12cd34")
        return (angebot(preis=89),)

    monkeypatch.setattr(watcher, "lade_angebote", lade_und_loesche)
    await store.fuege_hinzu(watch())
    bot = _bot()

    await watcher.pruefe_alle(bot, "t", HEUTE, erlaubt=ERLAUBT, jetzt=JETZT)

    bot.send_message.assert_not_awaited()
    assert store.lade_watches() == ()


async def test_erste_pruefung_speichert_nicht_selbst(monkeypatch):
    """Review HIGH-2: gemerkt wird erst nach erfolgreichem Versand (im Dialog)."""
    von = date.today() + timedelta(days=30)
    abflug = datetime.combine(von, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=8)
    treffer = angebot(preis=89, abflug=abflug.isoformat(), rueckflug=(abflug + timedelta(days=3)).isoformat())
    monkeypatch.setattr(watcher, "lade_angebote", AsyncMock(return_value=(treffer,)))
    w = await store.fuege_hinzu(watch(von=von, bis=von + timedelta(days=10)))

    neu, bestes, alarm = await watcher.erste_pruefung(w, "t")

    assert (alarm, bestes.preis, neu.gemeldeter_preis) == (True, 89, 89)
    assert store.lade_watches()[0].gemeldeter_preis is None


# ── Fehlermeldung an Fabio ────────────────────────────────────────────────────


async def test_fehler_erst_ab_schwelle_und_nur_einmal():
    bot = _bot()
    for _ in range(watcher._FEHLER_SCHWELLE + 2):
        await watcher._melde_fehlschlag(bot, 999, TravelpayoutsFehler("HTTP 401"))
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.kwargs["chat_id"] == 999


async def test_scheduler_setzt_fehlerzaehler_nach_erfolg_zurueck(monkeypatch):
    await store.speichere_status(fehlschlaege=2, gemeldet=True)
    monkeypatch.setattr(watcher, "pruefe_alle", AsyncMock())
    monkeypatch.setattr(watcher, "get_settings", lambda: MagicMock(flug_watch_interval="3600"))
    schlaf = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(watcher.asyncio, "sleep", schlaf)

    with pytest.raises(asyncio.CancelledError):
        await watcher.run_flug_watch_scheduler(_bot(), "t", ERLAUBT, _bot(), 999)

    status = store.lade_status()
    assert (status["fehlschlaege"], status["gemeldet"]) == (0, False)
    assert status["letzter_lauf"] is not None
    assert schlaf.await_args_list[1].args[0] == 3600


async def test_scheduler_ueberlebt_kaputten_store(monkeypatch):
    """Review M-5: ein Store-Fehler im Fehlerpfad darf den Scheduler nicht beenden."""
    monkeypatch.setattr(watcher, "pruefe_alle", AsyncMock(side_effect=TravelpayoutsFehler("HTTP 500")))
    monkeypatch.setattr(store, "speichere_status", AsyncMock(side_effect=OSError("Platte voll")))
    monkeypatch.setattr(watcher, "get_settings", lambda: MagicMock(flug_watch_interval="3600"))
    schlaf = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
    monkeypatch.setattr(watcher.asyncio, "sleep", schlaf)

    with pytest.raises(asyncio.CancelledError):
        await watcher.run_flug_watch_scheduler(_bot(), "t", ERLAUBT, _bot(), 999)

    assert watcher.pruefe_alle.await_count == 2


@pytest.mark.parametrize("roh, erwartet", [("5", 900), ("7200", 7200), ("6h", 21600), ("", 21600), (" 3600 ", 3600)])
def test_intervall_ist_robust(monkeypatch, roh, erwartet):
    """Review HIGH-1: ein Tippfehler darf weder Settings() noch den Scheduler brechen."""
    monkeypatch.setattr(watcher, "get_settings", lambda: MagicMock(flug_watch_interval=roh))
    assert watcher._intervall() == erwartet


def test_settings_akzeptieren_ungueltiges_intervall(monkeypatch):
    from agent.config import Settings

    monkeypatch.setenv("FLUG_WATCH_INTERVAL", "6h")
    assert Settings().flug_watch_interval == "6h"


@pytest.mark.parametrize(
    "letzter_lauf, erwartet",
    [
        (None, 60),
        ("kaputt", 60),
        ("2026-11-20T18:00:00", 60),  # ohne Zeitzone
        ((JETZT - timedelta(hours=1)).isoformat(), 5 * 3600),
        ((JETZT - timedelta(hours=7)).isoformat(), 60),
        ((JETZT + timedelta(hours=1)).isoformat(), 6 * 3600),
    ],
)
def test_erste_wartezeit_beruecksichtigt_letzten_lauf(letzter_lauf, erwartet):
    """Review L-3: Neustarts dürfen den Prüftakt nicht immer wieder zurücksetzen."""
    assert watcher._erste_wartezeit(6 * 3600, letzter_lauf, JETZT) == erwartet


# ── Texte ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "von, bis, erwartet",
    [
        (date(2027, 3, 1), date(2027, 3, 31), "März 2027"),
        (date(2027, 3, 1), date(2027, 4, 30), "März–April 2027"),
        (date(2026, 12, 1), date(2027, 1, 31), "Dezember 2026–Januar 2027"),
        (date(2027, 3, 15), date(2027, 3, 30), "15.03.–30.03.2027"),
        (date(2026, 12, 20), date(2027, 1, 5), "20.12.2026–05.01.2027"),
        (date(2027, 3, 15), date(2027, 3, 15), "15.03.2027"),
    ],
)
def test_zeitraum_text(von, bis, erwartet):
    assert zeitraum_text(von, bis) == erwartet


def test_alert_text_enthaelt_kerninfos():
    text = alert_text(watch(), angebot(preis=89))
    for teil in (
        "89 €",
        "Berlin",
        "Rom",
        "Fr 05.03.2027 06:10",
        "Mi 10.03.2027",
        "150 €",
        "https://www.aviasales.com/",
        "Cache",
    ):
        assert teil in text


def test_alert_text_baut_suchlink_wenn_api_keinen_liefert():
    text = alert_text(watch(), angebot(link=""))
    assert "https://www.aviasales.com/search/BER0503ROM10031" in text


def test_alert_text_nur_hin():
    text = alert_text(watch(hin_und_zurueck=False), angebot(rueckflug=None, link=""))
    assert "nur hin" in text
    assert "https://www.aviasales.com/search/BER0503ROM1" in text
    assert "Zurück" not in text


def test_watch_text_nennt_naechte_und_letzten_preis():
    text = watch_text(watch(naechte_min=5, naechte_max=9, letzter_preis=99))
    assert "5–9 Nächte" in text
    assert "zuletzt 99 €" in text
