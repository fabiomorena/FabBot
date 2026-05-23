"""Tests für bot/curator_scheduler.py – run_curator_scheduler Branches."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


async def _run_one_iteration(bot, chat_id, *, should_run_val, run_dry_run_val=None):
    """Führt genau eine Iteration der Scheduler-Schleife aus."""
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    async def mock_run_dry_run():
        return run_dry_run_val

    with (
        patch("bot.curator_scheduler.asyncio.sleep", sleep_mock),
        patch("bot.curator_scheduler.should_run", return_value=should_run_val),
        patch("bot.curator_scheduler.run_dry_run", side_effect=mock_run_dry_run),
    ):
        try:
            from bot.curator_scheduler import run_curator_scheduler

            await run_curator_scheduler(bot, chat_id)
        except asyncio.CancelledError:
            pass


class TestCuratorScheduler:
    async def test_skips_when_should_run_false(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()

        await _run_one_iteration(bot, 123, should_run_val=False)

        bot.send_message.assert_not_called()

    async def test_sends_report_when_should_run_true(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()

        await _run_one_iteration(bot, 123, should_run_val=True, run_dry_run_val="Bericht")

        bot.send_message.assert_called_once_with(chat_id=123, text="Bericht", parse_mode="Markdown")

    async def test_no_message_when_report_empty(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()

        await _run_one_iteration(bot, 123, should_run_val=True, run_dry_run_val="")

        bot.send_message.assert_not_called()

    async def test_exception_is_caught_and_logged(self, caplog):
        bot = MagicMock()
        bot.send_message = AsyncMock()

        async def boom():
            raise RuntimeError("Test-Fehler")

        sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with (
            patch("bot.curator_scheduler.asyncio.sleep", sleep_mock),
            patch("bot.curator_scheduler.should_run", return_value=True),
            patch("bot.curator_scheduler.run_dry_run", side_effect=boom),
        ):
            try:
                from bot.curator_scheduler import run_curator_scheduler

                await run_curator_scheduler(bot, 123)
            except asyncio.CancelledError:
                pass

        assert "Test-Fehler" in caplog.text
        bot.send_message.assert_not_called()
