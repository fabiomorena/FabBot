"""Tests für agent/proactive/focus_mode.py – Phase 221 (Issue #104)."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from agent.proactive.focus_mode import (
    HARD_MUTE,
    NORMAL,
    SOFT_MUTE,
    get_focus_state,
    get_idle_seconds,
    get_last_activity_ts,
    is_focus_muted,
)


@pytest.fixture()
def activity_file(tmp_path):
    """Leitet _ACTIVITY_FILE auf tmp_path um."""
    af = tmp_path / "activity.json"
    with patch("agent.proactive.focus_mode._ACTIVITY_FILE", af):
        yield af


@pytest.fixture(autouse=True)
def clear_settings_cache():
    from agent.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGetLastActivityTs:
    def test_returns_none_wenn_datei_fehlt(self, activity_file):
        assert get_last_activity_ts() is None

    def test_gibt_timestamp_zurück(self, activity_file):
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        activity_file.write_text(json.dumps({"last_activity": ts.isoformat()}))
        result = get_last_activity_ts()
        assert result is not None
        assert abs(result - ts.timestamp()) < 1.0

    def test_fehlertoleranz_bei_kaputtem_json(self, activity_file):
        activity_file.write_text("kein json{")
        assert get_last_activity_ts() is None

    def test_fehlertoleranz_bei_fehlendem_feld(self, activity_file):
        activity_file.write_text(json.dumps({"other_key": "val"}))
        assert get_last_activity_ts() is None


class TestGetIdleSeconds:
    def test_gibt_null_zurück_wenn_keine_daten(self, activity_file):
        assert get_idle_seconds() == 0.0

    def test_berechnet_idle_korrekt(self, activity_file):
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        activity_file.write_text(json.dumps({"last_activity": past.isoformat()}))
        idle = get_idle_seconds()
        assert 29 * 60 <= idle <= 31 * 60

    def test_idle_niemals_negativ(self, activity_file):
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        activity_file.write_text(json.dumps({"last_activity": future.isoformat()}))
        assert get_idle_seconds() == 0.0


class TestGetFocusState:
    def test_normal_bei_kurzer_inaktivität(self, activity_file):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        activity_file.write_text(json.dumps({"last_activity": recent.isoformat()}))
        assert get_focus_state() == NORMAL

    def test_soft_mute_nach_15_minuten(self, activity_file):
        past = datetime.now(timezone.utc) - timedelta(minutes=20)
        activity_file.write_text(json.dumps({"last_activity": past.isoformat()}))
        assert get_focus_state() == SOFT_MUTE

    def test_hard_mute_nach_60_minuten(self, activity_file):
        past = datetime.now(timezone.utc) - timedelta(minutes=90)
        activity_file.write_text(json.dumps({"last_activity": past.isoformat()}))
        assert get_focus_state() == HARD_MUTE

    def test_normal_wenn_keine_aktivität(self, activity_file):
        # Ohne activity.json → idle=0 → NORMAL
        assert get_focus_state() == NORMAL

    def test_respektiert_konfigurierbare_schwellenwerte(self, activity_file):
        past = datetime.now(timezone.utc) - timedelta(minutes=25)
        activity_file.write_text(json.dumps({"last_activity": past.isoformat()}))
        with patch.dict("os.environ", {"FOCUS_SOFT_MUTE_MIN": "30", "FOCUS_HARD_MUTE_MIN": "120"}):
            from agent.config import get_settings

            get_settings.cache_clear()
            assert get_focus_state() == NORMAL
        get_settings.cache_clear()


class TestIsFocusMuted:
    def test_normal_nie_gemuted(self, activity_file):
        with patch("agent.proactive.focus_mode.get_focus_state", return_value=NORMAL):
            assert is_focus_muted() is False
            assert is_focus_muted(priority="high") is False

    def test_soft_mute_blockiert_normale_priorität(self, activity_file):
        with patch("agent.proactive.focus_mode.get_focus_state", return_value=SOFT_MUTE):
            assert is_focus_muted() is True

    def test_soft_mute_blockiert_nicht_hohe_priorität(self, activity_file):
        with patch("agent.proactive.focus_mode.get_focus_state", return_value=SOFT_MUTE):
            assert is_focus_muted(priority="high") is False

    def test_hard_mute_blockiert_immer(self, activity_file):
        with patch("agent.proactive.focus_mode.get_focus_state", return_value=HARD_MUTE):
            assert is_focus_muted() is True
            assert is_focus_muted(priority="high") is True
