"""
tests/test_ph299_tts_timeout_retry.py – Phase 299 (Issue #321)

Rund 15 % der Requests an api.openai.com hängen (gemessen 14.08.2026: 5 von 33,
reproduziert mit curl und httpx außerhalb des Bots). TCP-Connect gelingt, danach
kommt kein Byte bis der Timeout greift. Die Ursache liegt außerhalb von FabBot,
die Auswirkungen nicht:

- bot/tts.py retryte nur bei {429, 503}. Ein Timeout fiel direkt durch zum
  edge-tts-Fallback – bei 30 s Client-Timeout wartete der Nutzer also bei jedem
  siebten Versuch eine halbe Minute auf die schlechtere Stimme.
- bot/health_check.py hatte keinen Retry, wodurch etwa jeder 6. Morgen-Report
  TTS grundlos rot meldete.

Beide behandeln einen einzelnen Timeout jetzt als das, was er ist: transient.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import bot.health_check as hc
import bot.tts as tts


def _client_mock(*antworten):
    """AsyncClient-Mock, der die Antworten der Reihe nach liefert.

    Exceptions in der Liste werden geworfen, alles andere zurückgegeben.
    """
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    call = AsyncMock(side_effect=list(antworten))
    client.post = call
    client.get = call
    return client


def _ok(status=200, content=b"audio"):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# bot/tts.py – Sprachausgabe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_timeout_dann_erfolg_liefert_audio():
    """Der Kern: ein einzelner Hänger darf nicht in den edge-tts-Fallback
    führen – der zweite Versuch gelingt statistisch in 85 % der Fälle."""
    client = _client_mock(httpx.ReadTimeout(""), _ok(content=b"echtes_audio"))

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await tts._synthesize_openai("Hallo")

    assert result == b"echtes_audio"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_tts_zwei_timeouts_fallen_auf_edge_tts_zurueck():
    """Hängt es zweimal, bleibt es beim Fallback – kein endloses Retry."""
    client = _client_mock(httpx.ReadTimeout(""), httpx.ReadTimeout(""))

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await tts._synthesize_openai("Hallo")

    assert result is None
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_tts_connect_timeout_wird_ebenso_wiederholt():
    """Nicht nur ReadTimeout – jede httpx.TimeoutException zählt."""
    client = _client_mock(httpx.ConnectTimeout(""), _ok())

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await tts._synthesize_openai("Hallo")

    assert result == b"audio"


@pytest.mark.asyncio
async def test_tts_timeout_retry_wartet_nicht():
    """Bei 429 ist Backoff sinnvoll, bei einem Hänger kostet er nur Zeit."""
    client = _client_mock(httpx.ReadTimeout(""), _ok())

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
        patch("bot.tts.asyncio.sleep", new_callable=AsyncMock) as schlaf,
    ):
        await tts._synthesize_openai("Hallo")

    schlaf.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_429_retry_bleibt_erhalten():
    """Regressionsschutz: das bisherige Verhalten bei 429 darf nicht kippen."""
    client = _client_mock(_ok(status=429), _ok())

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
        patch("bot.tts.asyncio.sleep", new_callable=AsyncMock) as schlaf,
    ):
        result = await tts._synthesize_openai("Hallo")

    assert result == b"audio"
    schlaf.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_andere_exception_faellt_weiterhin_durch():
    """Nur Timeouts werden wiederholt – ein echter Fehler nicht."""
    client = _client_mock(httpx.InvalidURL("kaputt"), _ok())

    with (
        patch("bot.tts._get_openai_api_key", return_value="sk-test"),
        patch("httpx.AsyncClient", return_value=client),
    ):
        result = await tts._synthesize_openai("Hallo")

    assert result is None
    assert client.post.await_count == 1


def test_tts_timeout_ist_kuerzer_als_vorher():
    """30 s Warten vor dem Fallback war der eigentliche Schmerz."""
    assert tts._TTS_TIMEOUT <= 15.0


# ---------------------------------------------------------------------------
# bot/health_check.py – Morgen-Report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_tts_timeout_dann_erfolg_ist_gruen():
    """Ein einzelner Aussetzer darf den Report nicht rot färben."""
    fake_settings = MagicMock()
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-test"
    client = _client_mock(httpx.ReadTimeout(""), _ok())

    with (
        patch("bot.health_check.get_settings", return_value=fake_settings),
        patch("httpx.AsyncClient", return_value=client),
    ):
        ok, detail = await hc._check_tts()

    assert ok is True
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_check_tts_zwei_timeouts_bleiben_rot():
    """Häuft es sich, ist es ein echtes Problem und gehört in den Report."""
    fake_settings = MagicMock()
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-test"
    client = _client_mock(httpx.ReadTimeout(""), httpx.ReadTimeout(""))

    with (
        patch("bot.health_check.get_settings", return_value=fake_settings),
        patch("httpx.AsyncClient", return_value=client),
    ):
        ok, detail = await hc._check_tts()

    assert ok is False
    assert "Timeout" in detail
    assert "2" in detail, "Anzahl der Versuche gehört in die Meldung"


@pytest.mark.asyncio
async def test_check_tts_401_wird_nicht_wiederholt():
    """Ein ungültiger Key wird beim zweiten Versuch nicht gültig."""
    fake_settings = MagicMock()
    fake_settings.openai_api_key.get_secret_value.return_value = "sk-test"
    client = _client_mock(_ok(status=401), _ok())

    with (
        patch("bot.health_check.get_settings", return_value=fake_settings),
        patch("httpx.AsyncClient", return_value=client),
    ):
        ok, detail = await hc._check_tts()

    assert ok is False
    assert "401" in detail
    assert client.get.await_count == 1


def test_check_tts_gesamtdauer_bleibt_im_rahmen():
    """Zwei Versuche dürfen den Check nicht länger machen als der eine vorher –
    sonst verzögert sich der ganze Report."""
    assert hc._TTS_CHECK_TIMEOUT * hc._TTS_CHECK_VERSUCHE <= hc._CHECK_TIMEOUT
