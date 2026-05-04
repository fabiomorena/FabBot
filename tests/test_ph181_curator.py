"""
tests/test_ph181_curator.py – Phase 181 (Issue #143)

Testet Background Curator: State, Idle-Detection, Trigger-Logik,
Proposal-Builder, Apply/Cancel, Dry-Run-Orchestrator.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "curator_state.json"


@pytest.fixture
def memory_db(tmp_path):
    f = tmp_path / "memory.db"
    f.write_bytes(b"")
    return f


@pytest.fixture
def sample_profile():
    return {
        "identity": {"name": "Fabio", "location": "Berlin"},
        "people": [
            {"name": "Steffi", "context": "Freundin"},
            {"name": "Marco", "context": "Kollege"},
            {"name": "Steffi M.", "context": "Steffi aus Berlin"},
        ],
        "projects": {
            "active": [
                {"name": "FabBot", "priority": "high"},
                {"name": "AltesProjekt", "priority": "low"},
            ]
        },
        "notes": [
            "[01.01.2026 10:00] Notiz A",
            "[02.01.2026 10:00] Notiz B",
            "[03.01.2026 10:00] Notiz A nochmal",
        ],
    }


@pytest.fixture
def pinned_profile():
    return {
        "people": [
            {"name": "Steffi", "context": "Freundin", "_pinned": True},
            {"name": "Marco", "context": "Kollege"},
        ],
        "notes": [],
    }


# ---------------------------------------------------------------------------
# State-Management
# ---------------------------------------------------------------------------


class TestState:
    def test_load_state_missing_file_returns_empty(self, state_file):
        from agent.proactive.curator import _load_state

        with patch("agent.proactive.curator._STATE_FILE", state_file):
            assert _load_state() == {}

    def test_save_load_roundtrip(self, state_file):
        from agent.proactive.curator import _load_state, _save_state

        data = {"last_run_at": "2026-05-04T10:00:00+00:00", "pending_proposal": None}
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            _save_state(data)
            result = _load_state()
        assert result["last_run_at"] == data["last_run_at"]

    def test_save_creates_parent_dir(self, tmp_path):
        from agent.proactive.curator import _save_state

        nested = tmp_path / "deep" / "dir" / "state.json"
        with patch("agent.proactive.curator._STATE_FILE", nested):
            _save_state({"x": 1})
        assert nested.exists()

    def test_invalidate_pending_removes_keys(self, state_file):
        from agent.proactive.curator import _invalidate_pending, _load_state

        state = {
            "pending_proposal": {"ops": []},
            "pending_base_hash": "abc123",
            "pending_expires_at": "2026-05-05T10:00:00+00:00",
            "last_run_at": "2026-05-04T10:00:00+00:00",
        }
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            _invalidate_pending(state)
            loaded = _load_state()
        assert "pending_proposal" not in loaded
        assert "pending_base_hash" not in loaded
        assert "last_run_at" in loaded


# ---------------------------------------------------------------------------
# Idle-Detection
# ---------------------------------------------------------------------------


class TestIdleDetection:
    def test_returns_seconds_since_mtime(self, memory_db):
        from agent.proactive.curator import get_idle_seconds

        with patch("agent.proactive.curator._MEMORY_DB", memory_db):
            idle = get_idle_seconds()
        assert idle >= 0

    def test_missing_db_returns_zero(self, tmp_path):
        from agent.proactive.curator import get_idle_seconds

        nonexistent = tmp_path / "no.db"
        nonexistent_log = tmp_path / "no.log"
        with (
            patch("agent.proactive.curator._MEMORY_DB", nonexistent),
            patch("agent.proactive.curator._FABBOT_LOG", nonexistent_log),
        ):
            result = get_idle_seconds()
        assert result == 0.0

    def test_old_db_returns_large_value(self, tmp_path):
        from agent.proactive.curator import get_idle_seconds
        import os
        import time

        db = tmp_path / "memory.db"
        db.write_bytes(b"")
        old_time = time.time() - 7200  # 2h ago
        os.utime(db, (old_time, old_time))
        with patch("agent.proactive.curator._MEMORY_DB", db):
            idle = get_idle_seconds()
        assert idle >= 7000


# ---------------------------------------------------------------------------
# Trigger-Logik
# ---------------------------------------------------------------------------


class TestShouldRun:
    def test_false_when_idle_too_short(self, state_file, memory_db):
        from agent.proactive.curator import should_run

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator._MEMORY_DB", memory_db),
            patch("agent.proactive.curator.get_idle_seconds", return_value=100.0),
            patch("agent.proactive.heartbeat.is_muted", return_value=False),
        ):
            assert not should_run()

    def test_false_when_last_run_too_recent(self, state_file):
        from agent.proactive.curator import should_run

        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        state_file.write_text(json.dumps({"last_run_at": recent}))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=10000.0),
            patch("agent.proactive.heartbeat.is_muted", return_value=False),
        ):
            assert not should_run()

    def test_false_when_muted(self, state_file):
        from agent.proactive.curator import should_run

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=10000.0),
            patch("agent.proactive.heartbeat.is_muted", return_value=True),
        ):
            assert not should_run()

    def test_true_when_all_conditions_met(self, state_file):
        from agent.proactive.curator import should_run

        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        state_file.write_text(json.dumps({"last_run_at": old}))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=10000.0),
            patch("agent.proactive.heartbeat.is_muted", return_value=False),
        ):
            assert should_run()

    def test_force_ignores_idle_and_cooldown(self, state_file):
        from agent.proactive.curator import should_run

        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        state_file.write_text(json.dumps({"last_run_at": recent}))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=0.0),
        ):
            assert should_run(force=True)

    def test_true_when_no_previous_run(self, state_file):
        from agent.proactive.curator import should_run

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=10000.0),
            patch("agent.proactive.heartbeat.is_muted", return_value=False),
        ):
            assert should_run()


# ---------------------------------------------------------------------------
# Pinned-Helpers
# ---------------------------------------------------------------------------


class TestPinnedHelpers:
    def test_filter_pinned_finds_pinned_items(self, pinned_profile):
        from agent.proactive.curator import _get_pinned_paths

        paths = _get_pinned_paths(pinned_profile)
        assert any("people" in p for p in paths)

    def test_filter_pinned_empty_profile(self):
        from agent.proactive.curator import _get_pinned_paths

        assert _get_pinned_paths({}) == set()

    def test_filter_pinned_no_pinned_items(self, sample_profile):
        from agent.proactive.curator import _get_pinned_paths

        assert _get_pinned_paths(sample_profile) == set()


# ---------------------------------------------------------------------------
# Proposal-Builder
# ---------------------------------------------------------------------------


class TestBuildProposal:
    def test_archives_stale_not_deletes(self, sample_profile):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [{"section": "projects.active", "index": 1, "reason": "veraltet"}],
            "duplicates": [],
            "redundant_notes": [],
            "summary": "1 veraltetes Projekt",
        }
        proposal = _build_proposal(sample_profile, analysis)
        target = proposal["target_profile"]
        # Item archiviert, nicht gelöscht
        assert len(target["archived"]) == 1
        assert target["archived"][0]["name"] == "AltesProjekt"
        assert "_archived_at" in target["archived"][0]
        assert "_archived_reason" in target["archived"][0]
        # Aus aktiven Projekten entfernt
        active_names = [p["name"] for p in target["projects"]["active"]]
        assert "AltesProjekt" not in active_names

    def test_archives_redundant_notes(self, sample_profile):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [],
            "duplicates": [],
            "redundant_notes": [{"indices": [0, 2], "keep_index": 0, "reason": "gleicher Inhalt"}],
            "summary": "",
        }
        proposal = _build_proposal(sample_profile, analysis)
        target = proposal["target_profile"]
        assert any(item.get("_archived_from") == "notes" for item in target["archived"])

    def test_archives_duplicate_keeps_one(self, sample_profile):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [],
            "duplicates": [{"section": "people", "indices": [0, 2], "keep_index": 0, "reason": "Steffi doppelt"}],
            "redundant_notes": [],
            "summary": "",
        }
        proposal = _build_proposal(sample_profile, analysis)
        target = proposal["target_profile"]
        people_names = [p["name"] for p in target["people"]]
        assert "Steffi" in people_names
        assert len([n for n in people_names if "Steffi" in n]) == 1
        assert any(item.get("_archived_from") == "people" for item in target["archived"])

    def test_pinned_items_untouched(self, pinned_profile):
        from agent.proactive.curator import _build_proposal

        analysis = {
            "stale": [{"section": "people", "index": 0, "reason": "veraltet"}],
            "duplicates": [],
            "redundant_notes": [],
            "summary": "",
        }
        proposal = _build_proposal(pinned_profile, analysis)
        target = proposal["target_profile"]
        # Pinned Item bleibt erhalten
        people_names = [p["name"] for p in target["people"]]
        assert "Steffi" in people_names
        assert target["archived"] == []

    def test_empty_analysis_no_operations(self, sample_profile):
        from agent.proactive.curator import _build_proposal

        analysis = {"stale": [], "duplicates": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        assert proposal["operations"] == []

    def test_existing_archived_block_preserved(self, sample_profile):
        from agent.proactive.curator import _build_proposal

        sample_profile["archived"] = [{"name": "AltesItem", "_archived_at": "2026-01-01T00:00:00+00:00"}]
        analysis = {
            "stale": [{"section": "projects.active", "index": 1, "reason": "veraltet"}],
            "duplicates": [],
            "redundant_notes": [],
            "summary": "",
        }
        proposal = _build_proposal(sample_profile, analysis)
        archived = proposal["target_profile"]["archived"]
        # Altes + neues archiviertes Item
        assert len(archived) == 2
        assert any(a.get("name") == "AltesItem" for a in archived)


# ---------------------------------------------------------------------------
# Report-Formatter
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_empty_operations_shows_clean_message(self, sample_profile):
        from agent.proactive.curator import _build_proposal, format_report

        analysis = {"stale": [], "duplicates": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        report = format_report(proposal, expires)
        assert "sauber" in report

    def test_report_contains_apply_command(self, sample_profile):
        from agent.proactive.curator import _build_proposal, format_report

        analysis = {
            "stale": [{"section": "projects.active", "index": 1, "reason": "veraltet"}],
            "duplicates": [],
            "redundant_notes": [],
            "summary": "1 veraltet",
        }
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        report = format_report(proposal, expires)
        assert "/curator apply" in report
        assert "/curator cancel" in report

    def test_report_contains_summary(self, sample_profile):
        from agent.proactive.curator import _build_proposal, format_report

        analysis = {"stale": [], "duplicates": [], "redundant_notes": [], "summary": "Profil ist gut"}
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        report = format_report(proposal, expires)
        assert "Profil ist gut" in report


# ---------------------------------------------------------------------------
# LLM-Analyse (Mock)
# ---------------------------------------------------------------------------


class TestAnalyzeProfile:
    @pytest.mark.asyncio
    async def test_returns_none_on_llm_error(self, sample_profile):
        from agent.proactive.curator import _analyze_profile

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM down")
        with patch("agent.llm.get_llm", return_value=mock_llm):
            result = await _analyze_profile(sample_profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self, sample_profile):
        from agent.proactive.curator import _analyze_profile

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Das ist kein JSON!")
        with patch("agent.llm.get_llm", return_value=mock_llm):
            result = await _analyze_profile(sample_profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, sample_profile):
        import asyncio
        from agent.proactive.curator import _analyze_profile

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = asyncio.TimeoutError()
        with patch("agent.llm.get_llm", return_value=mock_llm):
            result = await _analyze_profile(sample_profile)
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self, sample_profile):
        from agent.proactive.curator import _analyze_profile

        response_json = json.dumps(
            {
                "duplicates": [],
                "stale": [{"section": "projects.active", "index": 1, "reason": "alt"}],
                "redundant_notes": [],
                "summary": "1 veraltet",
            }
        )
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=response_json)
        with patch("agent.llm.get_llm", return_value=mock_llm):
            result = await _analyze_profile(sample_profile)
        assert result is not None
        assert len(result["stale"]) == 1

    @pytest.mark.asyncio
    async def test_parses_json_wrapped_in_codeblock(self, sample_profile):
        from agent.proactive.curator import _analyze_profile

        inner = json.dumps({"duplicates": [], "stale": [], "redundant_notes": [], "summary": ""})
        wrapped = f"```json\n{inner}\n```"
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content=wrapped)
        with patch("agent.llm.get_llm", return_value=mock_llm):
            result = await _analyze_profile(sample_profile)
        assert result is not None


# ---------------------------------------------------------------------------
# Dry-Run-Orchestrator
# ---------------------------------------------------------------------------


class TestRunDryRun:
    @pytest.mark.asyncio
    async def test_returns_none_on_empty_profile(self, state_file):
        from agent.proactive.curator import run_dry_run

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.load_profile_with_hash", return_value=({}, "abc")),
        ):
            result = await run_dry_run(force=True)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_failure(self, state_file, sample_profile):
        from agent.proactive.curator import run_dry_run

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.load_profile_with_hash", return_value=(sample_profile, "abc")),
            patch("agent.proactive.curator._analyze_profile", new_callable=AsyncMock, return_value=None),
        ):
            result = await run_dry_run(force=True)
        assert result is None

    @pytest.mark.asyncio
    async def test_saves_pending_proposal_to_state(self, state_file, sample_profile):
        from agent.proactive.curator import run_dry_run, _load_state

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": "OK"}
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.load_profile_with_hash", return_value=(sample_profile, "hash123")),
            patch("agent.proactive.curator._analyze_profile", new_callable=AsyncMock, return_value=analysis),
        ):
            await run_dry_run(force=True)
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            state = _load_state()
        assert state.get("pending_proposal") is not None
        assert state.get("pending_base_hash") == "hash123"
        assert state.get("pending_expires_at") is not None

    @pytest.mark.asyncio
    async def test_returns_report_string(self, state_file, sample_profile):
        from agent.proactive.curator import run_dry_run

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": "Alles gut"}
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.load_profile_with_hash", return_value=(sample_profile, "h1")),
            patch("agent.proactive.curator._analyze_profile", new_callable=AsyncMock, return_value=analysis),
        ):
            result = await run_dry_run(force=True)
        assert isinstance(result, str)
        assert "Curator" in result


# ---------------------------------------------------------------------------
# Apply / Cancel
# ---------------------------------------------------------------------------


class TestApplyPending:
    @pytest.mark.asyncio
    async def test_apply_without_pending_returns_error(self, state_file):
        from agent.proactive.curator import apply_pending

        with patch("agent.proactive.curator._STATE_FILE", state_file):
            success, msg = await apply_pending()
        assert not success
        assert "kein" in msg.lower() or "Kein" in msg

    @pytest.mark.asyncio
    async def test_apply_expired_proposal_returns_error(self, state_file, sample_profile):
        from agent.proactive.curator import apply_pending, _build_proposal

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        state = {
            "pending_proposal": proposal,
            "pending_base_hash": "abc",
            "pending_expires_at": expired,
        }
        state_file.write_text(json.dumps(state))
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            success, msg = await apply_pending()
        assert not success
        assert "abgelaufen" in msg.lower() or "Abgelaufen" in msg

    @pytest.mark.asyncio
    async def test_apply_success(self, state_file, sample_profile):
        from agent.proactive.curator import apply_pending, _build_proposal
        from agent.profile import WriteResult

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        state = {
            "pending_proposal": proposal,
            "pending_base_hash": "abc",
            "pending_expires_at": expires,
        }
        state_file.write_text(json.dumps(state))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=WriteResult.OK),
        ):
            success, msg = await apply_pending()
        assert success
        assert "konsolidiert" in msg.lower() or "Operation" in msg

    @pytest.mark.asyncio
    async def test_apply_stale_invalidates_proposal(self, state_file, sample_profile):
        from agent.proactive.curator import apply_pending, _build_proposal, _load_state
        from agent.profile import WriteResult

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        state = {
            "pending_proposal": proposal,
            "pending_base_hash": "abc",
            "pending_expires_at": expires,
        }
        state_file.write_text(json.dumps(state))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.profile.write_profile", new_callable=AsyncMock, return_value=WriteResult.STALE),
        ):
            success, msg = await apply_pending()
        assert not success
        assert "veraltet" in msg.lower() or "STALE" in msg or "geändert" in msg
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            remaining_state = _load_state()
        assert "pending_proposal" not in remaining_state


class TestCancelPending:
    def test_cancel_clears_pending(self, state_file, sample_profile):
        from agent.proactive.curator import cancel_pending, _load_state, _build_proposal

        analysis = {"duplicates": [], "stale": [], "redundant_notes": [], "summary": ""}
        proposal = _build_proposal(sample_profile, analysis)
        state = {"pending_proposal": proposal, "pending_base_hash": "abc"}
        state_file.write_text(json.dumps(state))
        with patch("agent.proactive.curator._STATE_FILE", state_file):
            msg = cancel_pending()
            remaining = _load_state()
        assert "verworfen" in msg.lower()
        assert "pending_proposal" not in remaining

    def test_cancel_without_pending_returns_message(self, state_file):
        from agent.proactive.curator import cancel_pending

        with patch("agent.proactive.curator._STATE_FILE", state_file):
            msg = cancel_pending()
        assert msg  # Irgendeine sinnvolle Meldung


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_status_shows_never_when_no_run(self, state_file):
        from agent.proactive.curator import get_status

        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=300.0),
        ):
            status = get_status()
        assert "noch nie" in status

    def test_status_shows_pending_when_proposal_exists(self, state_file, sample_profile):
        from agent.proactive.curator import get_status, _build_proposal

        analysis = {
            "stale": [{"section": "projects.active", "index": 1, "reason": "alt"}],
            "duplicates": [],
            "redundant_notes": [],
            "summary": "",
        }
        proposal = _build_proposal(sample_profile, analysis)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        state = {"pending_proposal": proposal, "pending_expires_at": expires}
        state_file.write_text(json.dumps(state))
        with (
            patch("agent.proactive.curator._STATE_FILE", state_file),
            patch("agent.proactive.curator.get_idle_seconds", return_value=300.0),
        ):
            status = get_status()
        assert "offen" in status or "Operationen" in status
