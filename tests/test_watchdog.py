"""Tests für watchdog.py Auto-Restart-Logik (Issue #105)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


def _make_state(**overrides) -> dict:
    base = {
        "last_status": "down",
        "down_since": (datetime.now() - timedelta(minutes=15)).isoformat(),
        "notified": True,
        "notified_at": (datetime.now() - timedelta(minutes=6)).isoformat(),
        "restart_count": 0,
        "last_restart_at": None,
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# _attempt_restart
# ---------------------------------------------------------------------------


class TestAttemptRestart:
    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up", return_value=True)
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run")
    @patch("watchdog.os.getuid", return_value=501)
    def test_success_updates_state(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0)
        from watchdog import _attempt_restart

        state = _make_state(restart_count=0)
        result = _attempt_restart(state)

        assert result["last_status"] == "up"
        assert result["restart_count"] == 1
        assert result["notified"] is False
        assert result["down_since"] is None
        mock_sleep.assert_called_once_with(60)

    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up", return_value=True)
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run")
    @patch("watchdog.os.getuid", return_value=501)
    def test_success_sends_correct_telegram(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0)
        from watchdog import _attempt_restart

        _attempt_restart(_make_state(restart_count=0))

        calls = [str(c) for c in mock_tg.call_args_list]
        assert any("automatisch neu gestartet" in c for c in calls)

    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up", return_value=False)
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run")
    @patch("watchdog.os.getuid", return_value=501)
    def test_failure_bot_not_started(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        mock_run.return_value = MagicMock(returncode=0)
        from watchdog import _attempt_restart

        state = _make_state(restart_count=0)
        result = _attempt_restart(state)

        assert result["last_status"] == "down"
        assert result["restart_count"] == 1
        assert result["last_restart_at"] is not None
        calls = [str(c) for c in mock_tg.call_args_list]
        assert any("fehlgeschlagen" in c for c in calls)

    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up", return_value=False)
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run")
    @patch("watchdog.os.getuid", return_value=501)
    def test_kickstart_error_no_sleep(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        mock_run.return_value = MagicMock(returncode=1)
        from watchdog import _attempt_restart

        _attempt_restart(_make_state(restart_count=0))

        mock_sleep.assert_not_called()

    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up", return_value=False)
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run")
    @patch("watchdog.os.getuid", return_value=501)
    def test_last_attempt_hints_manual_intervention(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        import watchdog as wd
        from watchdog import _attempt_restart

        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(wd, "WATCHDOG_MAX_RESTARTS", 3):
            _attempt_restart(_make_state(restart_count=2))

        calls = [str(c) for c in mock_tg.call_args_list]
        assert any("manuell" in c for c in calls)

    @patch("watchdog.time.sleep")
    @patch("watchdog._is_bot_up")
    @patch("watchdog._send_telegram", return_value=True)
    @patch("watchdog.subprocess.run", side_effect=Exception("timeout"))
    @patch("watchdog.os.getuid", return_value=501)
    def test_subprocess_exception_handled(self, mock_uid, mock_run, mock_tg, mock_up, mock_sleep):
        mock_up.return_value = False
        from watchdog import _attempt_restart

        result = _attempt_restart(_make_state(restart_count=0))

        assert result["restart_count"] == 1
        assert result["last_status"] == "down"


# ---------------------------------------------------------------------------
# main() – Integration
# ---------------------------------------------------------------------------


class TestMainAutoRestart:
    def _run_main(self, state: dict, bot_up: bool, env_overrides: dict | None = None):
        import watchdog as wd

        env = {
            "WATCHDOG_AUTO_RESTART": "true",
            "WATCHDOG_RESTART_DELAY_MIN": "5",
            "WATCHDOG_MAX_RESTARTS": "3",
            **(env_overrides or {}),
        }
        with (
            patch.object(wd, "BOT_TOKEN", "fake-token"),
            patch.object(wd, "CHAT_ID", "123"),
            patch.object(wd, "WATCHDOG_AUTO_RESTART", env["WATCHDOG_AUTO_RESTART"] == "true"),
            patch.object(wd, "WATCHDOG_RESTART_DELAY_MIN", int(env["WATCHDOG_RESTART_DELAY_MIN"])),
            patch.object(wd, "WATCHDOG_MAX_RESTARTS", int(env["WATCHDOG_MAX_RESTARTS"])),
            patch("watchdog._load_state", return_value=state),
            patch("watchdog._save_state") as mock_save,
            patch("watchdog._is_launch_agent_running", return_value=bot_up),
            patch("watchdog._is_python_process_running", return_value=bot_up),
            patch(
                "watchdog._attempt_restart", return_value={**state, "restart_count": state.get("restart_count", 0) + 1}
            ) as mock_restart,
            patch("watchdog._send_telegram", return_value=True),
        ):
            wd.main()
            return mock_save.call_args[0][0], mock_restart

    def test_restart_triggered_after_delay(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=6)).isoformat(),
            restart_count=0,
        )
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_called_once()

    def test_restart_not_triggered_before_delay(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=2)).isoformat(),
            restart_count=0,
        )
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_not_called()

    def test_restart_not_triggered_when_max_reached(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=10)).isoformat(),
            restart_count=3,
        )
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_not_called()

    def test_restart_disabled_via_feature_flag(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=10)).isoformat(),
            restart_count=0,
        )
        saved_state, mock_restart = self._run_main(
            state, bot_up=False, env_overrides={"WATCHDOG_AUTO_RESTART": "false"}
        )
        mock_restart.assert_not_called()

    def test_recovery_resets_restart_fields(self):
        state = _make_state(
            last_status="down",
            notified=True,
            restart_count=2,
            last_restart_at=(datetime.now() - timedelta(minutes=1)).isoformat(),
        )
        saved_state, _ = self._run_main(state, bot_up=True)

        assert saved_state["restart_count"] == 0
        assert saved_state["last_restart_at"] is None
        assert saved_state["notified"] is False

    def test_not_notified_no_restart(self):
        state = _make_state(notified=False, notified_at=None, restart_count=0)
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_not_called()

    def test_uses_last_restart_at_as_reference_for_second_attempt(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=30)).isoformat(),
            restart_count=1,
            last_restart_at=(datetime.now() - timedelta(minutes=6)).isoformat(),
        )
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_called_once()

    def test_second_attempt_too_soon_skipped(self):
        state = _make_state(
            notified_at=(datetime.now() - timedelta(minutes=30)).isoformat(),
            restart_count=1,
            last_restart_at=(datetime.now() - timedelta(minutes=2)).isoformat(),
        )
        saved_state, mock_restart = self._run_main(state, bot_up=False)
        mock_restart.assert_not_called()
