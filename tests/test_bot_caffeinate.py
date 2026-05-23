"""Tests für bot/caffeinate.py – start, stop, monitor."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import bot.caffeinate as caffeinate_module


@pytest.fixture(autouse=True)
def reset_proc():
    caffeinate_module._proc = None
    yield
    caffeinate_module._proc = None


class TestStart:
    def test_creates_process(self):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            caffeinate_module.start()

        mock_popen.assert_called_once()
        assert caffeinate_module._proc is mock_proc

    def test_passes_current_pid(self):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen, patch("os.getpid", return_value=5678):
            caffeinate_module.start()

        args = mock_popen.call_args[0][0]
        assert "5678" in args


class TestStop:
    def test_terminates_and_clears(self):
        mock_proc = MagicMock()
        caffeinate_module._proc = mock_proc

        caffeinate_module.stop()

        mock_proc.terminate.assert_called_once()
        assert caffeinate_module._proc is None

    def test_noop_when_no_process(self):
        caffeinate_module._proc = None
        caffeinate_module.stop()  # kein Fehler


class TestMonitor:
    async def test_exits_when_proc_is_none(self):
        caffeinate_module._proc = None

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await caffeinate_module.monitor()

    async def test_restarts_crashed_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        caffeinate_module._proc = mock_proc

        call_count = 0

        async def mock_sleep(_delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                caffeinate_module._proc = None

        new_proc = MagicMock()
        new_proc.pid = 999
        with patch("asyncio.sleep", side_effect=mock_sleep), patch("subprocess.Popen", return_value=new_proc):
            await caffeinate_module.monitor()

        assert call_count == 2

    async def test_healthy_process_not_restarted(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # läuft noch

        call_count = 0

        async def mock_sleep(_delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                caffeinate_module._proc = None

        caffeinate_module._proc = mock_proc
        with patch("asyncio.sleep", side_effect=mock_sleep), patch("subprocess.Popen") as mock_popen:
            await caffeinate_module.monitor()

        mock_popen.assert_not_called()
