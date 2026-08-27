"""
tests/test_caffeinate_task_referenz.py – Issue #331

Beim Shutdown stand in JEDEM Neustart ein ERROR im Log:

    [ERROR] asyncio: Task was destroyed but it is pending!
    task: <Task pending name='Task-6' coro=<monitor() running at bot/caffeinate.py:35>

Ursache: `asyncio.create_task(_caff_monitor())` warf die Referenz weg. Der Task
landete damit weder in `_scheduler_tasks` noch wurde er in `_post_shutdown`
gecancelt. Die acht Scheduler-Tasks werden alle sauber registriert – der
caffeinate-Monitor war der einzige Ausreißer.

Zwei Folgen: Rauschen im Log, das echte Fehler verdeckt (siehe Phase 230, wo
genau das die Diagnose blockierte), und laut asyncio-Doku darf der GC einen
Task ohne gehaltene Referenz mitten im Betrieb einsammeln – der
caffeinate-Watchdog könnte also unbemerkt verschwinden.
"""

import ast
import pathlib

import pytest


def _bot_py() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "bot" / "bot.py"


def test_caffeinate_monitor_wird_nicht_verwaist_gestartet():
    """Der caffeinate-Monitor läuft endlos und muss darum gehalten werden.

    Abgegrenzt gegen die kurzlebigen Fire-and-Forget-Tasks im selben Modul
    (`_warmup_whisper_delayed`, `_warmup_profile`, `collect_entities`,
    `extract_intentions`): die sind nach Sekunden fertig. Sie in
    `_scheduler_tasks` zu sammeln wäre sogar schädlich – die Liste wüchse bei
    jeder eingehenden Nachricht.
    """
    baum = ast.parse(_bot_py().read_text())

    for knoten in ast.walk(baum):
        # Ein Expr-Statement ist ein Aufruf, dessen Ergebnis niemand nimmt.
        if not isinstance(knoten, ast.Expr) or not isinstance(knoten.value, ast.Call):
            continue
        quelltext = ast.unparse(knoten.value)
        if "create_task" in quelltext and "_caff_monitor" in quelltext:
            pytest.fail(f"Zeile {knoten.lineno}: caffeinate-Monitor ohne gehaltene Referenz – {quelltext[:70]}")


def test_caffeinate_monitor_landet_in_scheduler_tasks():
    """Nur was registriert ist, wird in _post_shutdown gecancelt."""
    quelle = _bot_py().read_text()
    assert "_scheduler_tasks.append(task_caffeinate)" in quelle, (
        "caffeinate-Monitor muss wie die acht Scheduler-Tasks registriert werden"
    )


@pytest.mark.asyncio
async def test_post_shutdown_cancelt_alle_registrierten_tasks():
    """_post_shutdown muss jeden registrierten Task beenden – sonst bleibt beim
    Neustart wieder ein 'Task was destroyed' zurück."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import bot.bot as bot_mod

    async def laeuft_ewig():
        await asyncio.sleep(3600)

    task = asyncio.create_task(laeuft_ewig())

    with (
        patch.object(bot_mod, "_scheduler_tasks", [task]),
        patch("bot.caffeinate.stop"),
        patch("bot.bot.stop_service", new=AsyncMock()),
        patch("agent.supervisor.close_graph", new=AsyncMock()),
    ):
        await bot_mod._post_shutdown(object())

    await asyncio.sleep(0)
    assert task.cancelled() or task.done(), "Task lief nach dem Shutdown weiter"
