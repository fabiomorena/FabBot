"""
bot/caffeinate.py – Caffeinate-Lifecycle + Watchdog (Issue #197)

Verhindert Mac-Sleep solange der Bot läuft. Überwacht den caffeinate-Prozess
und startet ihn bei unerwartetem Absturz automatisch neu.
"""

import asyncio
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60
_proc: subprocess.Popen | None = None


def start() -> None:
    global _proc
    _proc = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    logger.info(f"caffeinate gestartet (PID {_proc.pid})")


def stop() -> None:
    global _proc
    if _proc is not None:
        _proc.terminate()
        _proc = None


async def monitor() -> None:
    global _proc
    while True:
        await asyncio.sleep(_CHECK_INTERVAL)
        if _proc is None:
            return
        if _proc.poll() is not None:
            logger.warning(f"caffeinate abgestürzt (exit {_proc.returncode}) – starte neu")
            start()
