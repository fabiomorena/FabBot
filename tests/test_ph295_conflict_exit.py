"""
tests/test_ph295_conflict_exit.py – Phase 295 (Issue #295), erweitert Phase 297

Regressionsschutz für den Conflict-Pfad im _error_handler.

Phase 295: Bei einem Telegram-Conflict muss der Prozess tatsächlich beendet
werden (SIGTERM), damit launchd sauber neu startet – statt nur das Polling zu
stoppen und als Zombie weiterzulaufen (Scheduler liefen, eingehende
Nachrichten wurden tagelang nicht mehr verarbeitet).

Phase 297: Der Exit darf aber nicht schon beim ERSTEN Conflict erfolgen.
Einzelne Conflicts sind transient – nach Netzwerkabbruch oder Sleep/Wake des
Macs hält Telegram die alte getUpdates-Verbindung kurz offen. Zusammen mit
launchd KeepAlive führte der sofortige Exit zu Neustarts im
Viertelstundentakt, jeweils mit "Bot gestartet."-Nachricht. Eine echte
Zweitinstanz pollt dauerhaft und erzeugt binnen Sekunden mehrere Conflicts.
"""

import signal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from telegram.error import Conflict, NetworkError

import bot.bot as bot_mod


def _build_app_with_token():
    """Baut die App offline mit Fake-Token und gibt den registrierten
    error_handler-Callback zurück."""
    fake_settings = MagicMock()
    fake_settings.telegram_bot_token.get_secret_value.return_value = "123456:FAKE-TOKEN"
    with patch("bot.bot.get_settings", return_value=fake_settings):
        app = bot_mod.build_bot()
    # PTB v20: app.error_handlers ist ein dict {callback: block}
    handlers = list(app.error_handlers.keys())
    assert len(handlers) == 1, f"Erwartet genau einen error_handler, gefunden: {len(handlers)}"
    return app, handlers[0]


def _conflict_context(app):
    return SimpleNamespace(error=Conflict("terminated by other getUpdates"), application=app)


@pytest.mark.asyncio
async def test_einzelner_conflict_beendet_prozess_nicht():
    """Phase 297: Ein transienter Conflict darf KEINEN Neustart auslösen."""
    app, error_handler = _build_app_with_token()

    with patch("bot.bot.os.kill") as mock_kill:
        await error_handler(object(), _conflict_context(app))

    mock_kill.assert_not_called()


@pytest.mark.asyncio
async def test_wiederholte_conflicts_loesen_sigterm_aus():
    """Phase 295: Eine echte Zweitinstanz (Conflicts in Folge) beendet den
    Prozess weiterhin – sonst kehrt der Zombie-Bug zurück."""
    app, error_handler = _build_app_with_token()
    context = _conflict_context(app)

    with patch("bot.bot.os.kill") as mock_kill, patch("bot.bot.os.getpid", return_value=4242):
        for _ in range(bot_mod._CONFLICT_EXIT_THRESHOLD):
            await error_handler(object(), context)

    mock_kill.assert_called_once_with(4242, signal.SIGTERM)


@pytest.mark.asyncio
async def test_conflicts_ausserhalb_des_fensters_beenden_nicht():
    """Vereinzelte Conflicts über Stunden verteilt (Sleep/Wake-Muster) dürfen
    sich nicht zum Exit aufsummieren – das Fenster muss sie verfallen lassen."""
    app, error_handler = _build_app_with_token()
    context = _conflict_context(app)

    # Jeder Conflict liegt weiter als das Fenster hinter dem vorherigen
    step = bot_mod._CONFLICT_WINDOW_SECONDS + 1
    fake_now = [1000.0]

    with patch("bot.bot.os.kill") as mock_kill, patch("bot.bot.time.monotonic", side_effect=lambda: fake_now[0]):
        for _ in range(bot_mod._CONFLICT_EXIT_THRESHOLD * 2):
            await error_handler(object(), context)
            fake_now[0] += step

    mock_kill.assert_not_called()


@pytest.mark.asyncio
async def test_netzwerkfehler_beendet_prozess_nicht():
    """Transiente Netzwerkfehler dürfen den Prozess NICHT beenden."""
    app, error_handler = _build_app_with_token()
    context = SimpleNamespace(error=NetworkError("temporär"), application=app)

    with patch("bot.bot.os.kill") as mock_kill:
        await error_handler(object(), context)

    mock_kill.assert_not_called()


@pytest.mark.asyncio
async def test_conflict_zaehler_ist_pro_app_isoliert():
    """Der Zähler darf nicht global sein – sonst summieren sich Conflicts
    verschiedener App-Instanzen auf (und Tests beeinflussen einander)."""
    app_a, handler_a = _build_app_with_token()
    app_b, handler_b = _build_app_with_token()

    with patch("bot.bot.os.kill") as mock_kill:
        for _ in range(bot_mod._CONFLICT_EXIT_THRESHOLD - 1):
            await handler_a(object(), _conflict_context(app_a))
        await handler_b(object(), _conflict_context(app_b))

    mock_kill.assert_not_called()


def test_kein_application_stop_im_conflict_pfad():
    """Strukturschutz: alter Zombie-Bug (application.stop statt Prozess-Exit)
    darf nicht zurückkehren."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "bot" / "bot.py").read_text()
    # Im Conflict-Zweig muss os.kill(...SIGTERM) stehen, kein application.stop()
    assert "os.kill(os.getpid(), signal.SIGTERM)" in source
    assert "context.application.stop()" not in source
