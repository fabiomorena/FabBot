"""
Tests für stop_service(): der Service-Prozess muss samt Kindprozessen sterben.

Vorher rief stop_service() nur _service_process.terminate() auf. Die
puppeteer-Chrome-Kinder überlebten den Bot-Neustart, hielten das
Session-Verzeichnis (SingletonLock) und der neu gespawnte Service blieb nach
`authenticated` auf ready:false stehen – ohne QR-Code. Man musste von Hand
mit pkill nachhelfen.

Jetzt nutzt stop_service() dieselbe Logik wie _terminate_orphan() (PR #310):
Parent + alle Kinder SIGTERM, warten, dann SIGKILL für die Überlebenden.
"""

import subprocess
import sys
import time

import psutil
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

# Ein Prozess, der ein eigenes Kind startet und dann wartet – Stellvertreter
# für den Node-Service mit seinem puppeteer-Chrome.
_ELTERN_SKRIPT = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
    "time.sleep(60)"
)

# Kind, das SIGTERM ignoriert – erzwingt den SIGKILL-Fallback.
_ELTERN_SKRIPT_STUR = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
    "time.sleep(60)"
)


def _warte_auf_kind(pid: int, timeout: float = 10.0) -> psutil.Process:
    """Wartet bis der Prozess sein Kind gespawnt hat und gibt es zurück."""
    frist = time.monotonic() + timeout
    while time.monotonic() < frist:
        kinder = psutil.Process(pid).children(recursive=True)
        if kinder:
            return kinder[0]
        time.sleep(0.05)
    raise AssertionError(f"Prozess {pid} hat innerhalb von {timeout}s kein Kind gestartet")


def _ist_tot(proc: psutil.Process, timeout: float = 10.0) -> bool:
    """True sobald der Prozess weg oder ein Zombie ist."""
    frist = time.monotonic() + timeout
    while time.monotonic() < frist:
        try:
            if proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def prozessbaum():
    """Echter Prozessbaum (Eltern + Kind); räumt am Ende zuverlässig auf."""
    gestartet: list[subprocess.Popen] = []

    def _start(skript: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", skript])
        gestartet.append(proc)
        return proc

    yield _start

    for proc in gestartet:
        try:
            eltern = psutil.Process(proc.pid)
            for p in [eltern, *eltern.children(recursive=True)]:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    continue
        except psutil.NoSuchProcess:
            pass
        proc.poll()


# ---------------------------------------------------------------------------
# Echte Prozesse – der eigentliche Bug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_service_beendet_auch_kindprozesse(prozessbaum):
    """Der Chrome-Stellvertreter darf stop_service() nicht überleben."""
    import bot.whatsapp as wa

    proc = prozessbaum(_ELTERN_SKRIPT)
    eltern = psutil.Process(proc.pid)
    kind = _warte_auf_kind(proc.pid)

    wa._service_process = proc
    await wa.stop_service()

    assert _ist_tot(kind), "Kindprozess (puppeteer-Chrome) hat stop_service() überlebt"
    assert _ist_tot(eltern), "Service-Prozess selbst läuft noch"
    assert wa._service_process is None


@pytest.mark.asyncio
async def test_stop_service_killt_sigterm_ignorierendes_kind(prozessbaum):
    """Kind, das SIGTERM ignoriert, wird nach dem Timeout hart gekillt."""
    import bot.whatsapp as wa

    proc = prozessbaum(_ELTERN_SKRIPT_STUR)
    kind = _warte_auf_kind(proc.pid)

    wa._service_process = proc
    with patch("bot.whatsapp._ORPHAN_TERM_TIMEOUT", 1):
        await wa.stop_service()

    assert _ist_tot(kind), "SIGTERM-resistentes Kind wurde nicht gekillt"


@pytest.mark.asyncio
async def test_stop_service_laesst_keinen_zombie_zurueck(prozessbaum):
    """Der Exitstatus wird eingesammelt – kein Zombie im Prozessbaum."""
    import bot.whatsapp as wa

    proc = prozessbaum(_ELTERN_SKRIPT)
    _warte_auf_kind(proc.pid)

    wa._service_process = proc
    await wa.stop_service()

    assert proc.poll() is not None, "Popen-Objekt kennt den Exitstatus nicht – Zombie bleibt"


@pytest.mark.asyncio
async def test_stop_service_blockiert_den_event_loop_nicht(prozessbaum):
    """Das Warten auf SIGTERM läuft im Thread – der Loop bleibt bedienbar.

    Grund: stop_service() hängt im async Shutdown-Hook; wait_procs() blockiert
    bis zu _ORPHAN_TERM_TIMEOUT Sekunden.
    """
    import asyncio

    import bot.whatsapp as wa

    proc = prozessbaum(_ELTERN_SKRIPT_STUR)
    _warte_auf_kind(proc.pid)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    wa._service_process = proc
    ticker = asyncio.create_task(_ticker())
    with patch("bot.whatsapp._ORPHAN_TERM_TIMEOUT", 1):
        await wa.stop_service()
    ticker.cancel()

    assert ticks >= 3, f"Event Loop war während stop_service() blockiert (nur {ticks} Ticks)"


# ---------------------------------------------------------------------------
# Randfälle – ohne echte Prozesse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_service_terminiert_die_prozessgruppe():
    """stop_service() räumt über _terminate_orphan() mit der Service-PID auf."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 4711
    wa._service_process = mock_proc

    with patch("bot.whatsapp._terminate_orphan", return_value=True) as mock_term:
        await wa.stop_service()

    mock_term.assert_called_once_with(4711)
    assert wa._service_process is None


@pytest.mark.asyncio
async def test_stop_service_ohne_prozess_raeumt_nicht():
    """_service_process=None → kein Aufräumversuch, kein Fehler."""
    import bot.whatsapp as wa

    wa._service_process = None
    with patch("bot.whatsapp._terminate_orphan") as mock_term:
        await wa.stop_service()

    mock_term.assert_not_called()


@pytest.mark.asyncio
async def test_stop_service_ueberspringt_beendeten_prozess():
    """Prozess bereits beendet → kein Aufräumversuch (PID könnte neu vergeben sein)."""
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.pid = 4711
    wa._service_process = mock_proc

    with patch("bot.whatsapp._terminate_orphan") as mock_term:
        await wa.stop_service()

    mock_term.assert_not_called()
    assert wa._service_process is None


@pytest.mark.asyncio
async def test_stop_service_gibt_referenz_auch_bei_fehler_frei():
    """Schlägt das Beenden fehl, wird _service_process trotzdem geleert.

    Sonst hielte der nächste start_service() den Dienst für laufend
    (poll() is None) und würde gar nicht erst neu starten.
    """
    import bot.whatsapp as wa

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 4711
    wa._service_process = mock_proc

    with patch("bot.whatsapp._terminate_orphan", return_value=False):
        await wa.stop_service()

    assert wa._service_process is None
