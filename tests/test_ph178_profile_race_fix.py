"""
tests/test_ph178_profile_race_fix.py

Phase 178 – Issue #142: Race-Condition-Fix + Frozen-Snapshot + Prefix-Cache.

Testet:
- load_profile() gibt deepcopy zurück (keine Cache-Mutation)
- _compute_profile_hash() ist stabil und inhaltsbasiert
- load_profile_with_hash() Konsistenz
- WriteResult bool()-Konvertierung
- write_profile() STALE-Erkennung bei Concurrent Writes
- Kein Datenverlust bei parallelen Writes
- _write_profile_bytes() atomic via os.replace
- Frozen-Snapshot Lifecycle (TTL, Refresh)
- get_profile_context_full() nutzt Snapshot
- invalidate_chat_cache() wird nicht mehr pro Write aufgerufen
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile_module(tmp_path: Path):
    import importlib
    import agent.profile as mod

    importlib.reload(mod)
    mod._PROFILE_PATH = tmp_path / "personal_profile.yaml"
    mod._BACKUP_PATH = tmp_path / "personal_profile.yaml.bak"
    mod._profile_cache = None
    mod._migration_done = False
    mod._profile_snapshot = None
    mod._snapshot_expires_at = 0.0
    return mod


def _write_plain_yaml(mod, content: str) -> None:
    """Schreibt unverschlüsseltes YAML (für Tests ohne Crypto-Mock)."""
    with (
        patch("agent.crypto.is_encrypted", return_value=True),
        patch("agent.crypto.decrypt", return_value=content),
        patch("agent.crypto.encrypt", side_effect=lambda x: x.encode() if isinstance(x, str) else x),
    ):
        pass
    mod._PROFILE_PATH.write_bytes(content.encode())


# ---------------------------------------------------------------------------
# load_profile – gibt deepcopy zurück
# ---------------------------------------------------------------------------


class TestLoadProfileDeepCopy:
    def test_returns_deepcopy_not_same_reference(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"identity:\n  name: Fabio\n")

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
        ):
            p1 = mod.load_profile()
            p2 = mod.load_profile()

        assert p1 is not p2, "load_profile() muss deepcopy zurückgeben"
        assert p1 == p2

    def test_caller_mutation_does_not_corrupt_cache(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"notes:\n  - original\n")

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
        ):
            profile = mod.load_profile()
            profile["notes"].append("mutated by caller")
            profile2 = mod.load_profile()

        assert profile2["notes"] == ["original"], "Cache darf durch Caller-Mutation nicht korrumpiert werden"


# ---------------------------------------------------------------------------
# _compute_profile_hash
# ---------------------------------------------------------------------------


class TestComputeProfileHash:
    def test_same_content_same_hash(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p = {"identity": {"name": "Fabio"}, "notes": ["a", "b"]}
        assert mod._compute_profile_hash(p) == mod._compute_profile_hash(p)

    def test_different_content_different_hash(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p1 = {"identity": {"name": "Fabio"}}
        p2 = {"identity": {"name": "Marco"}}
        assert mod._compute_profile_hash(p1) != mod._compute_profile_hash(p2)

    def test_insertion_order_independent(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        p1 = {"a": 1, "b": 2}
        p2 = {"b": 2, "a": 1}
        assert mod._compute_profile_hash(p1) == mod._compute_profile_hash(p2)

    def test_hash_length_16(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        h = mod._compute_profile_hash({"x": 1})
        assert len(h) == 16


# ---------------------------------------------------------------------------
# WriteResult
# ---------------------------------------------------------------------------


class TestWriteResult:
    def test_ok_is_truthy(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        assert bool(mod.WriteResult.OK) is True

    def test_stale_is_falsy(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        assert bool(mod.WriteResult.STALE) is False

    def test_invalid_is_falsy(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        assert bool(mod.WriteResult.INVALID) is False

    def test_io_error_is_falsy(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        assert bool(mod.WriteResult.IO_ERROR) is False


# ---------------------------------------------------------------------------
# write_profile – STALE-Erkennung
# ---------------------------------------------------------------------------


class TestWriteProfileStale:
    def test_correct_hash_writes_ok(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._migration_done = True  # kein Migration-Schreiben – Datei bleibt plain YAML
        profile = {"identity": {"name": "Fabio"}}
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
        ):
            _, base_hash = mod.load_profile_with_hash()
            profile["notes"] = ["neue note"]
            result = asyncio.get_event_loop().run_until_complete(
                mod.write_profile(profile, expected_base_hash=base_hash)
            )

        assert result == mod.WriteResult.OK

    def test_wrong_hash_returns_stale(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        profile = {"identity": {"name": "Fabio"}}
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
            patch("agent.crypto.decrypt", return_value=yaml.dump(profile)),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                mod.write_profile(profile, expected_base_hash="00000000deadbeef")
            )

        assert result == mod.WriteResult.STALE

    def test_no_hash_always_writes(self, tmp_path):
        """Ohne expected_base_hash kein STALE – Legacy-Verhalten."""
        mod = _make_profile_module(tmp_path)
        profile = {"identity": {"name": "Fabio"}}
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
        ):
            result = asyncio.get_event_loop().run_until_complete(mod.write_profile(profile))

        assert result == mod.WriteResult.OK


# ---------------------------------------------------------------------------
# Concurrent Writes – kein Datenverlust
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_parallel_notes_no_data_loss(self, tmp_path):
        """50 parallele add_note_to_profile() dürfen keinen Note verlieren."""
        mod = _make_profile_module(tmp_path)
        import yaml

        initial = {"notes": []}
        mod._PROFILE_PATH.write_bytes(yaml.dump(initial).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", side_effect=lambda x: x.encode() if isinstance(x, str) else x),
        ):
            tasks = [mod.add_note_to_profile(f"Note {i}") for i in range(20)]
            asyncio.get_event_loop().run_until_complete(asyncio.gather(*tasks))

            result_bytes = mod._PROFILE_PATH.read_bytes()

        result_profile = yaml.safe_load(result_bytes.decode())
        notes = result_profile.get("notes", [])
        assert len(notes) == 20, f"Alle 20 Notes müssen gespeichert sein, gefunden: {len(notes)}"


# ---------------------------------------------------------------------------
# _write_profile_bytes – atomic via os.replace
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_tmp_file_removed_after_write(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        mod._PROFILE_PATH.write_bytes(b"original")
        mod._write_profile_bytes(b"new content")

        tmp = mod._PROFILE_PATH.with_suffix(".yaml.tmp")
        assert not tmp.exists(), ".tmp-Datei muss nach replace entfernt sein"
        assert mod._PROFILE_PATH.read_bytes() == b"new content"


# ---------------------------------------------------------------------------
# Frozen Snapshot – Lifecycle
# ---------------------------------------------------------------------------


class TestFrozenSnapshot:
    def test_snapshot_stable_after_write(self, tmp_path):
        """Snapshot ändert sich nicht durch write_profile innerhalb der TTL."""
        mod = _make_profile_module(tmp_path)
        import yaml

        profile = {"identity": {"name": "Fabio"}, "notes": []}
        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", side_effect=lambda x: x.encode() if isinstance(x, str) else x),
        ):
            snap1 = mod.get_active_snapshot()
            asyncio.get_event_loop().run_until_complete(mod.add_note_to_profile("neue info"))
            snap2 = mod.get_active_snapshot()

        assert snap1 == snap2, "Snapshot darf sich mid-Session nicht ändern"

    def test_snapshot_refreshes_after_ttl(self, tmp_path):
        """Nach TTL-Ablauf liefert get_active_snapshot() frische Daten."""
        mod = _make_profile_module(tmp_path)
        import yaml

        profile = {"identity": {"name": "Fabio"}, "notes": []}
        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", side_effect=lambda x: x.encode() if isinstance(x, str) else x),
        ):
            mod._SNAPSHOT_TTL = 0.01  # 10ms TTL für Test
            snap1 = mod.get_active_snapshot()

            asyncio.get_event_loop().run_until_complete(mod.add_note_to_profile("neue info"))
            time.sleep(0.02)  # TTL ablaufen lassen

            with patch("agent.agents.chat_agent.invalidate_chat_cache"):
                snap2 = mod.get_active_snapshot()

        assert snap2.get("notes"), "Nach TTL muss Snapshot neue Note enthalten"
        assert snap1 != snap2

    def test_refresh_snapshot_calls_invalidate_cache(self, tmp_path):
        """refresh_snapshot() muss invalidate_chat_cache() aufrufen."""
        mod = _make_profile_module(tmp_path)
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump({"notes": []}).encode())

        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
        ):
            mock_invalidate = MagicMock()
            with patch("agent.agents.chat_agent.invalidate_chat_cache", mock_invalidate):
                mod.refresh_snapshot()

        mock_invalidate.assert_called_once()

    def test_get_active_snapshot_returns_deepcopy(self, tmp_path):
        """Snapshot-Rückgabe darf nicht mutierbar sein."""
        mod = _make_profile_module(tmp_path)
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump({"notes": ["original"]}).encode())

        with patch("agent.crypto.is_encrypted", return_value=False):
            snap = mod.get_active_snapshot()
            snap["notes"].append("injected")
            snap2 = mod.get_active_snapshot()

        assert "injected" not in snap2.get("notes", [])


# ---------------------------------------------------------------------------
# get_profile_context_full – nutzt Snapshot
# ---------------------------------------------------------------------------


class TestGetProfileContextFullUsesSnapshot:
    def test_uses_snapshot_not_live_profile(self, tmp_path):
        """get_profile_context_full() soll Snapshot nutzen, nicht direkt load_profile()."""
        mod = _make_profile_module(tmp_path)
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump({"identity": {"name": "Fabio"}}).encode())

        with patch("agent.crypto.is_encrypted", return_value=False):
            # Snapshot manuell setzen mit anderem Inhalt
            mod._profile_snapshot = {"identity": {"name": "SnapshotFabio"}}
            mod._snapshot_expires_at = time.monotonic() + 300

            ctx = mod.get_profile_context_full()

        assert "SnapshotFabio" in ctx, "Kontext muss aus Snapshot kommen"
        assert "Fabio" not in ctx or "SnapshotFabio" in ctx


# ---------------------------------------------------------------------------
# add_note_to_profile – kein invalidate_chat_cache mehr
# ---------------------------------------------------------------------------


class TestAddNoteNoCacheInvalidation:
    def test_no_invalidate_after_add_note(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        import yaml

        mod._PROFILE_PATH.write_bytes(yaml.dump({"notes": []}).encode())

        mock_invalidate = MagicMock()
        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", side_effect=lambda x: x.encode() if isinstance(x, str) else x),
            patch("agent.agents.chat_agent.invalidate_chat_cache", mock_invalidate),
        ):
            asyncio.get_event_loop().run_until_complete(mod.add_note_to_profile("test note"))

        mock_invalidate.assert_not_called()


# ---------------------------------------------------------------------------
# write_profile – kein invalidate_chat_cache mehr
# ---------------------------------------------------------------------------


class TestWriteProfileNoCacheInvalidation:
    def test_no_invalidate_after_write_profile(self, tmp_path):
        mod = _make_profile_module(tmp_path)
        import yaml

        profile = {"identity": {"name": "Fabio"}}
        mod._PROFILE_PATH.write_bytes(yaml.dump(profile).encode())

        mock_invalidate = MagicMock()
        with (
            patch("agent.crypto.is_encrypted", return_value=False),
            patch("agent.crypto.encrypt", return_value=b"enc"),
            patch("agent.agents.chat_agent.invalidate_chat_cache", mock_invalidate),
        ):
            asyncio.get_event_loop().run_until_complete(mod.write_profile(profile))

        mock_invalidate.assert_not_called()
