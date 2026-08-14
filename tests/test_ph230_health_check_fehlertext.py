"""
tests/test_ph230_health_check_fehlertext.py – Phase 230 (Issue #319)

Regressionsschutz gegen Health-Report-Zeilen ohne Fehlergrund.

Am 14.08.2026 meldete der Report um 06:00 nur "❌ TTS: " – ohne Text. Ursache:
httpx.ReadTimeout/ConnectTimeout erben NICHT von asyncio.TimeoutError, der
Timeout-Zweig griff also nicht, und ihr str() ist leer. Übrig blieb
`return False, str(e)[:80]` mit einem leeren String.

Zusätzlich loggte run_health_check nur "OK"/"PROBLEME" – welcher Check
gescheitert war, ließ sich nachträglich nicht mehr feststellen, weil der
Report ausschließlich per Telegram rausgeht.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import bot.health_check as hc


# ---------------------------------------------------------------------------
# _fehlertext – nie leer
# ---------------------------------------------------------------------------


def test_httpx_timeout_erbt_nicht_von_asyncio_timeout():
    """Die Annahme, auf der der alte Code stand, ist falsch – festnageln,
    damit der Timeout-Zweig nicht wieder auf asyncio.TimeoutError reduziert
    wird."""
    assert not issubclass(httpx.ReadTimeout, asyncio.TimeoutError)
    assert not issubclass(httpx.ConnectTimeout, asyncio.TimeoutError)
    assert str(httpx.ReadTimeout("")) == ""


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout(""),
        httpx.ConnectTimeout(""),
        httpx.ConnectError(""),
        httpx.HTTPError(""),
        ValueError(""),
        RuntimeError(),
    ],
)
def test_fehlertext_ist_nie_leer(exc):
    """Jede Exception muss einen nicht-leeren Grund liefern – sonst steht im
    Report wieder nur '❌ TTS: '."""
    text = hc._fehlertext(exc)
    assert text.strip(), f"leerer Fehlertext für {type(exc).__name__}"


@pytest.mark.parametrize("exc", [httpx.ReadTimeout(""), httpx.ConnectTimeout("")])
def test_httpx_timeout_wird_als_timeout_ausgewiesen(exc):
    text = hc._fehlertext(exc)
    assert "Timeout" in text
    assert type(exc).__name__ in text


def test_fehlertext_faellt_auf_klassennamen_zurueck():
    assert hc._fehlertext(httpx.ConnectError("")) == "ConnectError"


def test_fehlertext_behaelt_vorhandene_meldung():
    assert hc._fehlertext(ValueError("Datei fehlt")) == "Datei fehlt"


def test_fehlertext_kuerzt_auf_80_zeichen():
    assert len(hc._fehlertext(ValueError("x" * 500))) == 80


# ---------------------------------------------------------------------------
# _check_tts – der konkrete Fall aus dem Report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_tts_bei_timeout_mit_grund():
    """Der Fall vom 14.08.2026: Timeout gegen api.openai.com darf nicht als
    leerer String im Report landen."""
    fake_settings = MagicMock()
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-test"

    client = AsyncMock()
    client.get.side_effect = httpx.ReadTimeout("")

    with (
        patch("bot.health_check.get_settings", return_value=fake_settings),
        patch("httpx.AsyncClient") as mock_cls,
    ):
        mock_cls.return_value.__aenter__.return_value = client
        ok, detail = await hc._check_tts()

    assert ok is False
    assert detail.strip(), "leerer Grund – genau der Bug aus #319"
    assert "Timeout" in detail


# ---------------------------------------------------------------------------
# run_health_check – fehlgeschlagene Checks landen im Log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fehlgeschlagene_checks_werden_geloggt(caplog):
    """Ohne Log-Eintrag ist der Report nach dem Telegram-Versand verloren –
    genau das machte die Nachdiagnose am 14.08. unmöglich."""
    bot = AsyncMock()

    async def _fail():
        return False, "Timeout (ReadTimeout)"

    async def _ok():
        return True, "alles gut"

    with (
        patch.object(hc, "_check_tts", _fail),
        patch.object(hc, "_check_terminal", _ok),
        patch.object(hc, "_check_anthropic", _ok),
        patch.object(hc, "_check_web", _ok),
        patch.object(hc, "_check_calendar", _ok),
        patch.object(hc, "_check_profile", _ok),
        patch.object(hc, "_check_memory_db", _ok),
        patch.object(hc, "_check_disk_space", _ok),
        patch.object(hc, "_check_chromadb", _ok),
        patch.object(hc, "_check_whatsapp", _ok),
        patch.object(hc, "_check_audit_log", _ok),
        patch.object(hc, "_check_heartbeat", _ok),
        patch.object(hc, "_check_schedulers", _ok),
        caplog.at_level("WARNING", logger="bot.health_check"),
    ):
        await hc.run_health_check(bot, chat_id=1)

    protokoll = caplog.text
    assert "TTS" in protokoll
    assert "Timeout (ReadTimeout)" in protokoll


@pytest.mark.asyncio
async def test_gather_exception_liefert_grund_statt_leer(caplog):
    """asyncio.gather(return_exceptions=True) reicht die Exception durch –
    auch dort darf kein leerer Text entstehen."""
    bot = AsyncMock()

    async def _boom():
        raise httpx.ConnectTimeout("")

    async def _ok():
        return True, "alles gut"

    with (
        patch.object(hc, "_check_tts", _boom),
        patch.object(hc, "_check_terminal", _ok),
        patch.object(hc, "_check_anthropic", _ok),
        patch.object(hc, "_check_web", _ok),
        patch.object(hc, "_check_calendar", _ok),
        patch.object(hc, "_check_profile", _ok),
        patch.object(hc, "_check_memory_db", _ok),
        patch.object(hc, "_check_disk_space", _ok),
        patch.object(hc, "_check_chromadb", _ok),
        patch.object(hc, "_check_whatsapp", _ok),
        patch.object(hc, "_check_audit_log", _ok),
        patch.object(hc, "_check_heartbeat", _ok),
        patch.object(hc, "_check_schedulers", _ok),
        caplog.at_level("WARNING", logger="bot.health_check"),
    ):
        await hc.run_health_check(bot, chat_id=1)

    gesendet = bot.send_message.await_args.kwargs["text"]
    assert "❌ TTS: \n" not in gesendet
    assert "ConnectTimeout" in gesendet


@pytest.mark.asyncio
async def test_kein_log_rauschen_wenn_alles_gruen(caplog):
    """Bei grünem Check darf keine WARNING-Zeile entstehen."""
    bot = AsyncMock()

    async def _ok():
        return True, "alles gut"

    namen = [
        "_check_terminal",
        "_check_anthropic",
        "_check_web",
        "_check_calendar",
        "_check_profile",
        "_check_memory_db",
        "_check_disk_space",
        "_check_chromadb",
        "_check_whatsapp",
        "_check_audit_log",
        "_check_tts",
        "_check_heartbeat",
        "_check_schedulers",
    ]
    with patch.multiple(hc, **{n: _ok for n in namen}):
        with caplog.at_level("WARNING", logger="bot.health_check"):
            await hc.run_health_check(bot, chat_id=1)

    assert caplog.text.strip() == ""
