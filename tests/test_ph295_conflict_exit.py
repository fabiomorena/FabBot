"""
tests/test_ph295_conflict_exit.py – Phase 295 (Issue #295)

Regressionsschutz: Bei einem Telegram-Conflict muss der _error_handler den
Prozess tatsächlich beenden (SIGTERM), damit launchd sauber neu startet –
statt nur das Polling zu stoppen und als Zombie weiterzulaufen.

Ergänzt am 12.08.2026: Beendet wird erst bei Häufung (STANDARD_SCHWELLE), da
ein einzelner Conflict meist transient ist. Der Prozess-Exit als solcher
bleibt Pflicht – siehe test_wiederholter_conflict_loest_sigterm_aus.

Hintergrund: Vorher rief der Handler asyncio.create_task(application.stop())
auf. Das stoppte nur das Polling; der Prozess lebte weiter, die Scheduler
liefen, eingehende Nachrichten wurden nicht mehr verarbeitet (tagelang).
"""

import signal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from telegram.error import Conflict, NetworkError


def _build_app_with_token():
    """Baut die App offline mit Fake-Token und gibt den registrierten
    error_handler-Callback zurück."""
    fake_settings = MagicMock()
    fake_settings.telegram_bot_token.get_secret_value.return_value = "123456:FAKE-TOKEN"
    with patch("bot.bot.get_settings", return_value=fake_settings):
        import bot.bot as bot_mod

        app = bot_mod.build_bot()
    # PTB v20: app.error_handlers ist ein dict {callback: block}
    handlers = list(app.error_handlers.keys())
    assert len(handlers) == 1, f"Erwartet genau einen error_handler, gefunden: {len(handlers)}"
    return app, handlers[0]


@pytest.mark.asyncio
async def test_wiederholter_conflict_loest_sigterm_aus():
    """Conflict in Häufung → der Prozess wird via SIGTERM beendet (launchd-Neustart).

    Seit dem 12.08.2026 erst ab STANDARD_SCHWELLE statt beim ersten Conflict –
    der Prozess-Exit als solcher bleibt aber Pflicht (Zombie-Schutz, s.o.).
    """
    from bot.conflict_tracker import STANDARD_SCHWELLE

    app, error_handler = _build_app_with_token()
    context = SimpleNamespace(error=Conflict("terminated by other getUpdates"), application=app)

    with patch("bot.bot.os.kill") as mock_kill, patch("bot.bot.os.getpid", return_value=4242):
        for _ in range(STANDARD_SCHWELLE):
            await error_handler(object(), context)

    mock_kill.assert_called_once_with(4242, signal.SIGTERM)


@pytest.mark.asyncio
async def test_einzelner_conflict_beendet_prozess_nicht():
    """Ein transienter Conflict (eigene alte getUpdates-Verbindung) darf den
    Prozess NICHT beenden – sonst entsteht die Neustart-Schleife vom 12.08.2026."""
    app, error_handler = _build_app_with_token()
    context = SimpleNamespace(error=Conflict("terminated by other getUpdates"), application=app)

    with patch("bot.bot.os.kill") as mock_kill, patch("bot.bot.os.getpid", return_value=4242):
        await error_handler(object(), context)

    mock_kill.assert_not_called()


@pytest.mark.asyncio
async def test_netzwerkfehler_beendet_prozess_nicht():
    """Transiente Netzwerkfehler dürfen den Prozess NICHT beenden."""
    app, error_handler = _build_app_with_token()
    context = SimpleNamespace(error=NetworkError("temporär"), application=app)

    with patch("bot.bot.os.kill") as mock_kill:
        await error_handler(object(), context)

    mock_kill.assert_not_called()


def test_kein_application_stop_im_conflict_pfad():
    """Strukturschutz: alter Zombie-Bug (application.stop statt Prozess-Exit)
    darf nicht zurückkehren."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "bot" / "bot.py").read_text()
    # Im Conflict-Zweig muss os.kill(...SIGTERM) stehen, kein application.stop()
    assert "os.kill(os.getpid(), signal.SIGTERM)" in source
    assert "context.application.stop()" not in source
