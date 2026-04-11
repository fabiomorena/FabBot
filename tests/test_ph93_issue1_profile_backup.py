"""
tests/test_ph93_issue1_profile_backup.py

Phase 93 – Issue #1: Backup vor destruktivem Profilschreiben.

Testet dass _write_profile_bytes() immer ein Backup anlegt bevor
personal_profile.yaml überschrieben wird. Behavioral Tests – kein
inspect.getsource(), kein PosixPath.write_bytes patching.
"""
import asyncio
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile_module(tmp_path: Path):
    """Importiert profile mit überschriebenem _PROFILE_PATH / _BACKUP_PATH."""
    import importlib
    import agent.profile as profile_mod
    importlib.reload(profile_mod)
    profile_mod._PROFILE_PATH = tmp_path / "personal_profile.yaml"
    profile_mod._BACKUP_PATH = tmp_path / "personal_profile.yaml.bak"
    profile_mod._profile_cache = None
    profile_mod._migration_done = False
    return profile_mod


# ---------------------------------------------------------------------------
# _write_profile_bytes – direkte Unit Tests (keine Mocks nötig)
# ---------------------------------------------------------------------------

class TestWriteProfileBytes:

    def test_backup_created_when_profile_exists(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        original_data = b"original encrypted content"
        mod._PROFILE_PATH.write_bytes(original_data)

        mod._write_profile_bytes(b"new encrypted content")

        assert mod._BACKUP_PATH.exists(), "Backup muss erstellt werden"
        assert mod._BACKUP_PATH.read_bytes() == original_data

    def test_new_data_written_to_profile(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"old data")

        mod._write_profile_bytes(b"new data")

        assert mod._PROFILE_PATH.read_bytes() == b"new data"

    def test_no_backup_when_profile_missing(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._write_profile_bytes(b"first write")

        assert not mod._BACKUP_PATH.exists()
        assert mod._PROFILE_PATH.read_bytes() == b"first write"

    def test_backup_overwritten_on_second_write(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"version 1")
        mod._write_profile_bytes(b"version 2")
        mod._write_profile_bytes(b"version 3")

        assert mod._BACKUP_PATH.read_bytes() == b"version 2"
        assert mod._PROFILE_PATH.read_bytes() == b"version 3"

    def test_profile_path_created_on_first_write(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        assert not mod._PROFILE_PATH.exists()
        mod._write_profile_bytes(b"initial data")
        assert mod._PROFILE_PATH.exists()


# ---------------------------------------------------------------------------
# add_note_to_profile – prüft dass _write_profile_bytes aufgerufen wird
# Strategie: _write_profile_bytes patchen + Backup-Logik in spy nachbauen
# (lokale crypto-Imports in der Funktion sind nicht über agent.crypto patchbar)
# ---------------------------------------------------------------------------

class TestAddNoteBackup:

    def test_write_profile_bytes_called_by_add_note(self, tmp_path):
        """add_note_to_profile() muss _write_profile_bytes() aufrufen."""
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"FABBOT_ENC_V1:token")

        call_count = []

        original_fn = mod._write_profile_bytes
        def spy(data):
            call_count.append(data)
            original_fn(data)

        with patch("agent.crypto.is_encrypted", return_value=True), \
             patch("agent.crypto.decrypt", return_value="notes: []\n"), \
             patch("agent.crypto.encrypt", return_value=b"FABBOT_ENC_V1:newtoken"), \
             patch.object(mod, "_write_profile_bytes", side_effect=spy):
            asyncio.get_event_loop().run_until_complete(
                mod.add_note_to_profile("Testnotiz")
            )

        assert len(call_count) == 1, "_write_profile_bytes genau einmal aufgerufen"

    def test_backup_exists_after_add_note_via_write_bytes(self, tmp_path):
        """Backup entsteht weil _write_profile_bytes backup anlegt – E2E."""
        mod = _make_profile_module(tmp_path)
        original = b"FABBOT_ENC_V1:original"
        mod._PROFILE_PATH.write_bytes(original)

        with patch("agent.crypto.is_encrypted", return_value=True), \
             patch("agent.crypto.decrypt", return_value="notes: []\n"), \
             patch("agent.crypto.encrypt", return_value=b"FABBOT_ENC_V1:new"):
            asyncio.get_event_loop().run_until_complete(
                mod.add_note_to_profile("Testnotiz")
            )

        assert mod._BACKUP_PATH.exists(), "Backup muss existieren"
        assert mod._BACKUP_PATH.read_bytes() == original

    def test_no_write_when_profile_missing(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        result = asyncio.get_event_loop().run_until_complete(
            mod.add_note_to_profile("Test")
        )
        assert result is False
        assert not mod._BACKUP_PATH.exists()


# ---------------------------------------------------------------------------
# write_profile – Backup-Verhalten
# ---------------------------------------------------------------------------

class TestWriteProfileBackup:

    def test_backup_created_after_write_profile(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        original_data = b"FABBOT_ENC_V1:originaltoken"
        mod._PROFILE_PATH.write_bytes(original_data)

        with patch("agent.crypto.encrypt", return_value=b"FABBOT_ENC_V1:newtoken"):
            result = asyncio.get_event_loop().run_until_complete(
                mod.write_profile({"identity": {"name": "Fabio"}})
            )

        assert result is True
        assert mod._BACKUP_PATH.exists()
        assert mod._BACKUP_PATH.read_bytes() == original_data

    def test_no_write_on_round_trip_mismatch(self, tmp_path):
        """Round-Trip-Mismatch → write_profile bricht vor _write_profile_bytes ab."""
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"original")

        write_called = []
        original_fn = mod._write_profile_bytes
        def spy(data):
            write_called.append(data)
            original_fn(data)

        with patch.object(mod, "_write_profile_bytes", side_effect=spy), \
             patch("yaml.safe_load", return_value={"identity": {"name": "DIFFERENT"}}), \
             patch("yaml.dump", return_value="identity:\n  name: Fabio\n"):
            result = asyncio.get_event_loop().run_until_complete(
                mod.write_profile({"identity": {"name": "Fabio"}})
            )

        assert result is False
        assert len(write_called) == 0, "_write_profile_bytes darf nicht aufgerufen werden"

    def test_empty_profile_rejected(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"original")
        result = asyncio.get_event_loop().run_until_complete(
            mod.write_profile({})
        )
        assert result is False


# ---------------------------------------------------------------------------
# Migration – Backup-Verhalten
# ---------------------------------------------------------------------------

class TestMigrationBackup:

    def test_backup_created_during_migration(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        plain_yaml = b"identity:\n  name: Fabio\n"
        mod._PROFILE_PATH.write_bytes(plain_yaml)

        with patch("agent.crypto.is_encrypted", return_value=False), \
             patch("agent.crypto.encrypt", return_value=b"FABBOT_ENC_V1:migrated"), \
             patch("agent.crypto.decrypt", return_value="identity:\n  name: Fabio\n"):
            mod.load_profile()

        assert mod._BACKUP_PATH.exists(), "Backup muss während Migration erstellt werden"
        assert mod._BACKUP_PATH.read_bytes() == plain_yaml
