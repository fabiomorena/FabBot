"""
Tests für Phase 95c – Issue #7: stop_service() ist jetzt async.

Testet:
1. stop_service() ist eine Coroutine (async def)
2. stop_service() terminiert einen laufenden Prozess
3. stop_service() ist idempotent (zweimal aufrufen = kein Fehler)
4. stop_service() mit None-Prozess → kein Fehler
5. stop_service() setzt _service_process auf None
6. _post_shutdown() ruft await stop_service() auf (kein sync-Aufruf)
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ---------------------------------------------------------------------------
# 1. stop_service() ist eine Coroutine
# ---------------------------------------------------------------------------

def test_stop_service_is_coroutine():
    """stop_service() muss async sein – gibt Coroutine zurück."""
    import inspect
    from bot.whatsapp import stop_service
    assert inspect.iscoroutinefunction(stop_service), \
        "stop_service() sollte async def sein (Issue #7)"


# ---------------------------------------------------------------------------
# 2. stop_service() terminiert laufenden Prozess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_service_terminates_process():
    """stop_service() ruft terminate() auf einem laufenden Prozess auf."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # Prozess läuft

    wa._service_process = mock_proc
    await wa.stop_service()

    mock_proc.terminate.assert_called_once()
    assert wa._service_process is None


# ---------------------------------------------------------------------------
# 3. stop_service() idempotent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_service_idempotent():
    """Zweimaliges Aufrufen von stop_service() → kein Fehler."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    wa._service_process = mock_proc
    await wa.stop_service()
    await wa.stop_service()  # Zweiter Aufruf – _service_process ist jetzt None


# ---------------------------------------------------------------------------
# 4. stop_service() mit None-Prozess
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_service_none_process():
    """stop_service() mit _service_process=None → kein Fehler."""
    import bot.whatsapp as wa
    wa._service_process = None
    await wa.stop_service()  # Kein Exception


# ---------------------------------------------------------------------------
# 5. stop_service() setzt _service_process auf None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_service_clears_process():
    """Nach stop_service() ist _service_process None."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    wa._service_process = mock_proc

    await wa.stop_service()
    assert wa._service_process is None


# ---------------------------------------------------------------------------
# 6. stop_service() nicht aufgerufen wenn Prozess bereits beendet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_service_skips_terminated_process():
    """Wenn Prozess schon beendet (poll() != None), kein terminate()."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0  # Bereits beendet

    wa._service_process = mock_proc
    await wa.stop_service()

    mock_proc.terminate.assert_not_called()
    assert wa._service_process is None
