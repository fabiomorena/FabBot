"""
tests/test_ph247_flug_app.py – Phase 247 (Issue #385)

Lebenszyklus des Flug-Bots im FabBot-Prozess: Aktivierung, Isolation,
Zugriffsschranke. Kernanforderung: ein Fehler im Flug-Bot darf FabBot nie
abbrechen, und ohne FLUG_BOT_TOKEN ändert sich nichts.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from telegram.ext import ApplicationHandlerStop, TypeHandler

from bot.flug import app as flug_app


@pytest.fixture(autouse=True)
def kein_laufender_bot(monkeypatch):
    monkeypatch.setattr(flug_app, "_laufend", None)
    monkeypatch.setattr(flug_app, "_start_task", None)
    yield


def _settings(**abweichungen) -> SimpleNamespace:
    basis = {
        "flug_bot_token": SecretStr("flug:token"),
        "flug_allowed_user_ids": "111, 222",
        "telegram_bot_token": SecretStr("haupt:token"),
        "travelpayouts_token": SecretStr(""),
    }
    return SimpleNamespace(**{**basis, **abweichungen})


def _fehler_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── IDs ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "roh, erwartet",
    [("111, 222", frozenset({111, 222})), ("", frozenset()), ("111,abc", frozenset()), (" , ", frozenset())],
)
def test_parse_ids(roh, erwartet):
    assert flug_app.parse_ids(roh) == erwartet


# ── Aktivierung ───────────────────────────────────────────────────────────────


async def test_ohne_token_deaktiviert(monkeypatch):
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings(flug_bot_token=SecretStr("")))
    bauen = MagicMock()
    monkeypatch.setattr(flug_app, "build_flug_app", bauen)
    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is False
    bauen.assert_not_called()


async def test_ohne_allowlist_deaktiviert(monkeypatch):
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings(flug_allowed_user_ids="kaputt"))
    bauen = MagicMock()
    monkeypatch.setattr(flug_app, "build_flug_app", bauen)
    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is False
    bauen.assert_not_called()


async def test_gleicher_token_wie_fabbot_wird_abgelehnt(monkeypatch):
    """Gleicher Token hieße: beide pollen denselben Bot – und die Isolation wäre weg."""
    monkeypatch.setattr(
        flug_app,
        "get_settings",
        lambda: _settings(flug_bot_token=SecretStr("x:y"), telegram_bot_token=SecretStr("x:y")),
    )
    bauen = MagicMock()
    monkeypatch.setattr(flug_app, "build_flug_app", bauen)
    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is False
    bauen.assert_not_called()


def _app_mock() -> MagicMock:
    app = MagicMock()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.updater.start_polling = AsyncMock()
    app.updater.stop = AsyncMock()
    return app


async def test_startfehler_bricht_nicht_ab_und_loggt_keinen_token(monkeypatch, caplog):
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings())
    app = _app_mock()
    app.initialize = AsyncMock(side_effect=RuntimeError("The token `flug:token` was rejected by the server."))
    monkeypatch.setattr(flug_app, "build_flug_app", lambda token, erlaubt: app)
    fehler_bot = _fehler_bot()

    with caplog.at_level(logging.DEBUG):
        assert await flug_app.starte_flug_bot(fehler_bot, 999) is False

    assert "flug:token" not in caplog.text
    assert "flug:token" not in str(fehler_bot.send_message.await_args)
    assert flug_app._laufend is None


async def test_start_und_stop(monkeypatch):
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings())
    app = _app_mock()
    erhalten = {}

    def bauen(token, erlaubt):
        erhalten.update(token=token, erlaubt=erlaubt)
        return app

    monkeypatch.setattr(flug_app, "build_flug_app", bauen)

    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is True
    assert erhalten == {"token": "flug:token", "erlaubt": frozenset({111, 222})}
    assert app.updater.start_polling.await_args.kwargs["drop_pending_updates"] is True

    await flug_app.stoppe_flug_bot()
    app.updater.stop.assert_awaited_once()
    app.stop.assert_awaited_once()
    app.shutdown.assert_awaited_once()
    assert flug_app._laufend is None


async def test_scheduler_nur_mit_travelpayouts_token(monkeypatch):
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings(travelpayouts_token=SecretStr("tp")))
    monkeypatch.setattr(flug_app, "build_flug_app", lambda token, erlaubt: _app_mock())
    scheduler = AsyncMock()
    monkeypatch.setattr(flug_app, "run_flug_watch_scheduler", scheduler)

    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is True
    task = flug_app._laufend.task
    assert task is not None
    await flug_app.stoppe_flug_bot()
    assert task.cancelled() or task.done()


async def test_stop_ohne_laufenden_bot_ist_noop():
    await flug_app.stoppe_flug_bot()


async def test_stop_faengt_fehler_ab(monkeypatch):
    app = _app_mock()
    app.updater.stop = AsyncMock(side_effect=RuntimeError("not running"))
    monkeypatch.setattr(flug_app, "_laufend", flug_app._Laufend(app=app, task=None))
    await flug_app.stoppe_flug_bot()
    app.shutdown.assert_awaited_once()


# ── Zugriffsschranke ──────────────────────────────────────────────────────────


def _update(user_id: int | None, chat_typ: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_user = None if user_id is None else SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(type=chat_typ)
    update.effective_message.reply_text = AsyncMock()
    return update


async def test_schranke_laesst_erlaubte_durch():
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    await schranke(_update(111), MagicMock())


async def test_schranke_stoppt_fremde_und_nennt_ihre_id():
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    update = _update(555)
    with pytest.raises(ApplicationHandlerStop):
        await schranke(update, MagicMock())
    antwort = update.effective_message.reply_text.await_args.args[0]
    assert "555" in antwort


async def test_schranke_stoppt_updates_ohne_nutzer():
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    with pytest.raises(ApplicationHandlerStop):
        await schranke(_update(None), MagicMock())


def test_schranke_laeuft_vor_allen_handlern():
    app = flug_app.build_flug_app("123:abc", frozenset({111}))
    assert sorted(app.handlers) == [-1, 0]
    (schranke,) = app.handlers[-1]
    assert isinstance(schranke, TypeHandler)
    assert len(app.handlers[0]) >= 5


async def test_schranke_stoppt_gruppenchats_auch_fuer_erlaubte():
    """Security L8: Alarme und Lösch-Buttons gehören nicht in Gruppen."""
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    update = _update(111, chat_typ="group")
    with pytest.raises(ApplicationHandlerStop):
        await schranke(update, MagicMock())
    assert "privat" in update.effective_message.reply_text.await_args.args[0]


async def test_schranke_ist_fail_closed():
    """Review M-6 / Security L7: wirft die Schranke selbst, bleibt sie trotzdem zu."""

    class KaputteMenge(frozenset):
        def __contains__(self, item):
            raise RuntimeError("kaputt")

    schranke = flug_app.erstelle_schranke(KaputteMenge({111}))
    with pytest.raises(ApplicationHandlerStop):
        await schranke(_update(111), MagicMock())


async def test_schranke_antwortet_fremden_nur_einmal():
    """Security L9: kein Antwort-Spam an Fremde."""
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    update = _update(555)
    for _ in range(3):
        with pytest.raises(ApplicationHandlerStop):
            await schranke(update, MagicMock())
    assert update.effective_message.reply_text.await_count == 1


async def test_schranke_haelt_wenn_antwort_scheitert():
    schranke = flug_app.erstelle_schranke(frozenset({111}))
    update = _update(555)
    update.effective_message.reply_text = AsyncMock(side_effect=RuntimeError("Telegram weg"))
    with pytest.raises(ApplicationHandlerStop):
        await schranke(update, MagicMock())


async def test_fehler_handler_stoppt_weitere_verarbeitung():
    with pytest.raises(ApplicationHandlerStop):
        await flug_app._fehler_handler(None, SimpleNamespace(error=RuntimeError("x")))


async def test_polling_nur_fuer_nachrichten_und_klicks(monkeypatch):
    """Review M-3: bearbeitete Nachrichten kommen gar nicht erst an."""
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings())
    app = _app_mock()
    monkeypatch.setattr(flug_app, "build_flug_app", lambda token, erlaubt: app)
    assert await flug_app.starte_flug_bot(_fehler_bot(), 999) is True
    assert app.updater.start_polling.await_args.kwargs["allowed_updates"] == ["message", "callback_query"]
    await flug_app.stoppe_flug_bot()


async def test_hintergrundstart_wird_beim_stop_abgebrochen_und_aufgeraeumt(monkeypatch):
    """Review L-7/M-4: ein hängender Start blockiert weder FabBots Start noch den Shutdown."""
    monkeypatch.setattr(flug_app, "get_settings", lambda: _settings())
    app = _app_mock()
    haengt = asyncio.Event()
    app.initialize = AsyncMock(side_effect=haengt.wait)
    monkeypatch.setattr(flug_app, "build_flug_app", lambda token, erlaubt: app)

    task = flug_app.starte_im_hintergrund(_fehler_bot(), 999)
    await asyncio.sleep(0)
    assert not task.done()

    await asyncio.wait_for(flug_app.stoppe_flug_bot(), timeout=1)
    assert task.cancelled()
    app.shutdown.assert_awaited_once()
    assert flug_app._laufend is None
