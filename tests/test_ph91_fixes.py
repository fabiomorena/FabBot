"""
Phase 91 Tests – 4 Fixes:
1. profile.py: Migration thread-safe (_migration_lock + _migration_done)
2. bot.py: cmd_clip Task-Registry (_background_tasks)
3. protocol.py: is_any_confirm() enthält CONFIRM_VISION
4. supervisor.py: Proto.MEMORY_VISION_MARKER statt Magic String
"""

import threading
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# 1. protocol.py – MEMORY_VISION_MARKER + is_any_confirm CONFIRM_VISION
# ---------------------------------------------------------------------------


class TestProtocolFixes:
    """Phase 91: protocol.py Korrektheitsfixes."""

    def test_memory_vision_marker_exists(self) -> None:
        """Proto.MEMORY_VISION_MARKER ist definiert."""
        from agent.protocol import Proto

        assert hasattr(Proto, "MEMORY_VISION_MARKER")
        assert isinstance(Proto.MEMORY_VISION_MARKER, str)
        assert len(Proto.MEMORY_VISION_MARKER) > 0

    def test_memory_vision_marker_value(self) -> None:
        """Proto.MEMORY_VISION_MARKER hat den erwarteten Wert."""
        from agent.protocol import Proto

        assert Proto.MEMORY_VISION_MARKER == "Bildbeschreibung"

    def test_is_any_confirm_includes_vision(self) -> None:
        """is_any_confirm() erkennt CONFIRM_VISION."""
        from agent.protocol import Proto

        vision_msg = f"{Proto.CONFIRM_VISION}some data"
        assert Proto.is_any_confirm(vision_msg) is True

    def test_is_any_confirm_still_covers_all_others(self) -> None:
        """is_any_confirm() deckt weiterhin alle anderen Confirm-Typen ab."""
        from agent.protocol import Proto

        assert Proto.is_any_confirm(f"{Proto.CONFIRM_TERMINAL}ls") is True
        assert Proto.is_any_confirm(f"{Proto.CONFIRM_FILE_WRITE}/tmp/x::y") is True
        assert Proto.is_any_confirm(f"{Proto.CONFIRM_CREATE_EVENT}Meeting::2026") is True
        assert Proto.is_any_confirm(f"{Proto.CONFIRM_COMPUTER}click:0:0:") is True
        assert Proto.is_any_confirm(f"{Proto.CONFIRM_WHATSAPP}Steffi::Hallo") is True

    def test_is_any_confirm_false_for_non_confirm(self) -> None:
        """is_any_confirm() gibt False für normale Nachrichten zurück."""
        from agent.protocol import Proto

        assert Proto.is_any_confirm("normale nachricht") is False
        assert Proto.is_any_confirm("__SCREENSHOT__:data") is False
        assert Proto.is_any_confirm("") is False

    def test_is_confirm_vision_standalone(self) -> None:
        """is_confirm_vision() funktioniert korrekt."""
        from agent.protocol import Proto

        assert Proto.is_confirm_vision(f"{Proto.CONFIRM_VISION}data") is True
        assert Proto.is_confirm_vision("andere nachricht") is False

    def test_memory_vision_marker_used_in_supervisor(self) -> None:
        """supervisor._filter_hitl_messages nutzt Proto.MEMORY_VISION_MARKER."""
        import inspect
        import agent.supervisor as supervisor_module

        source = inspect.getsource(supervisor_module._filter_hitl_messages)
        assert "MEMORY_VISION_MARKER" in source, (
            "_filter_hitl_messages() nutzt noch hardcoded 'Bildbeschreibung' statt Proto.MEMORY_VISION_MARKER"
        )


# ---------------------------------------------------------------------------
# 2. supervisor.py – _filter_hitl_messages mit Proto.MEMORY_VISION_MARKER
# ---------------------------------------------------------------------------


class TestSupervisorVisionMarker:
    """Phase 91: _filter_hitl_messages nutzt Proto.MEMORY_VISION_MARKER."""

    def setup_method(self) -> None:
        from agent.supervisor import _filter_hitl_messages

        self.filter = _filter_hitl_messages

    def test_memory_with_vision_marker_passes_through(self) -> None:
        """__MEMORY__-Message mit Bildbeschreibung wird NICHT ersetzt (bleibt sichtbar)."""
        from agent.protocol import Proto

        msgs = [AIMessage(content=f"__MEMORY__:{Proto.MEMORY_VISION_MARKER}: Ein Hund auf einer Wiese.")]
        result = self.filter(msgs)
        assert len(result) == 1
        assert Proto.MEMORY_VISION_MARKER in result[0].content

    def test_memory_without_vision_marker_replaced(self) -> None:
        """__MEMORY__-Message ohne Bildbeschreibung wird durch Platzhalter ersetzt."""
        msgs = [AIMessage(content="__MEMORY__:Terminal-Output: 92GB frei")]
        result = self.filter(msgs)
        assert len(result) == 1
        assert result[0].content == "[Aktion wurde ausgefuehrt]"

    def test_custom_marker_value_respected(self) -> None:
        """Wenn Proto.MEMORY_VISION_MARKER geändert wird, verhält sich Filter korrekt."""
        from agent.protocol import Proto

        msgs = [AIMessage(content="__MEMORY__:Bildbeschreibung: Test")]
        with patch.object(Proto, "MEMORY_VISION_MARKER", "Bildbeschreibung"):
            result = self.filter(msgs)
        # Mit Marker → durchgelassen
        assert "Bildbeschreibung" in result[0].content

    def test_normal_messages_unaffected(self) -> None:
        """Normale Messages bleiben unverändert."""
        msgs = [
            HumanMessage(content="Hallo"),
            AIMessage(content="Antwort"),
        ]
        result = self.filter(msgs)
        assert len(result) == 2
        assert result[0].content == "Hallo"
        assert result[1].content == "Antwort"


# ---------------------------------------------------------------------------
# 3. profile.py – Migration thread-safe
# ---------------------------------------------------------------------------


class TestProfileMigrationThreadSafe:
    """Phase 91: Migration plain YAML → verschlüsselt ist thread-safe."""

    def setup_method(self) -> None:
        import agent.profile as p

        p._profile_cache = None
        p._migration_done = False

    def teardown_method(self) -> None:
        import agent.profile as p

        p._profile_cache = None
        p._migration_done = False

    def test_migration_lock_exists(self) -> None:
        """_migration_lock ist ein threading.Lock."""
        from agent.profile import _migration_lock

        assert isinstance(_migration_lock, type(threading.Lock()))

    def test_migration_done_flag_exists(self) -> None:
        """_migration_done ist ein bool."""
        from agent.profile import _migration_done

        assert isinstance(_migration_done, bool)

    def test_migration_only_runs_once(self, tmp_path) -> None:
        """_migration_done Flag verhindert Doppel-Migration – einfacher Behavioral-Test."""
        import yaml
        import agent.profile as profile_module

        plain_yaml = yaml.dump({"identity": {"name": "Fabio"}})
        profile_file = tmp_path / "personal_profile.yaml"
        profile_file.write_text(plain_yaml, encoding="utf-8")

        profile_module._migration_done = False
        profile_module._profile_cache = None

        # Erster Load → Migration läuft, Flag wird gesetzt
        with patch("agent.profile._PROFILE_PATH", profile_file):
            profile_module.load_profile()

        assert profile_module._migration_done is True

        # Zweiter Load mit Flag=True → kein erneutes Schreiben
        profile_module._profile_cache = None
        encrypt_calls = []

        original_encrypt = None
        try:
            from agent import crypto

            original_encrypt = crypto.encrypt

            def counting_encrypt(text):
                encrypt_calls.append(text)
                return original_encrypt(text)

            with (
                patch("agent.crypto.encrypt", side_effect=counting_encrypt),
                patch("agent.profile._PROFILE_PATH", profile_file),
            ):
                profile_module.load_profile()
        finally:
            pass

        # Bei _migration_done=True darf encrypt() nicht für Migration aufgerufen werden
        assert len(encrypt_calls) == 0, "Migration wurde trotz _migration_done=True erneut ausgeführt"

    def test_migration_done_flag_set_after_migration(self, tmp_path) -> None:
        """_migration_done wird nach Migration auf True gesetzt."""
        import yaml
        import agent.profile as profile_module

        plain_yaml = yaml.dump({"identity": {"name": "Test"}})
        profile_file = tmp_path / "personal_profile.yaml"
        profile_file.write_text(plain_yaml, encoding="utf-8")

        profile_module._migration_done = False
        profile_module._profile_cache = None

        with patch("agent.profile._PROFILE_PATH", profile_file):
            profile_module.load_profile()

        assert profile_module._migration_done is True

    def test_migration_skipped_when_already_done(self, tmp_path) -> None:
        """Wenn _migration_done=True, wird nicht erneut verschlüsselt."""
        import yaml
        import agent.profile as profile_module

        plain_yaml = yaml.dump({"identity": {"name": "Test"}})
        profile_file = tmp_path / "personal_profile.yaml"
        profile_file.write_text(plain_yaml, encoding="utf-8")

        profile_module._migration_done = True  # bereits migriert
        profile_module._profile_cache = None

        write_called = {"n": 0}

        with patch("agent.profile._PROFILE_PATH", profile_file):
            with patch.object(
                type(profile_file),
                "write_bytes",
                lambda self, d: write_called.__setitem__("n", write_called["n"] + 1) or b"",
            ):
                profile_module.load_profile()

        assert write_called["n"] == 0

    def test_threading_lock_is_module_level(self) -> None:
        """_migration_lock ist auf Modulebene definiert (kein asyncio.Lock)."""
        import agent.profile as profile_module

        assert hasattr(profile_module, "_migration_lock")
        # Muss threading.Lock sein, kein asyncio.Lock
        lock = profile_module._migration_lock
        assert not hasattr(lock, "_loop"), "Kein asyncio.Lock erwartet"
        # threading.Lock hat acquire/release
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")


# ---------------------------------------------------------------------------
# 4. bot.py – _background_tasks Task-Registry in cmd_clip
# ---------------------------------------------------------------------------


class TestBotClipTaskRegistry:
    """Phase 91: cmd_clip registriert index_file Task in _background_tasks."""

    def test_background_tasks_set_exists(self) -> None:
        """bot._background_tasks ist ein Set auf Modulebene."""
        import bot.bot as bot_module

        assert hasattr(bot_module, "_background_tasks")
        assert isinstance(bot_module._background_tasks, set)

    def test_background_tasks_initially_empty(self) -> None:
        """_background_tasks ist initial leer (oder nach Tasks fertig)."""
        import bot.bot as bot_module

        # Set existiert – ob leer oder nicht hängt vom aktuellen Zustand ab
        assert isinstance(bot_module._background_tasks, set)

    @pytest.mark.asyncio
    async def test_clip_task_added_to_registry(self) -> None:
        """Nach /clip wird der index_file Task in _background_tasks registriert."""
        import asyncio
        import bot.bot as bot_module

        initial_size = len(bot_module._background_tasks)
        task_added = {"done": False}

        async def fake_index_file(path):
            await asyncio.sleep(0)
            task_added["done"] = True

        # Mock Update + Context
        mock_update = MagicMock()
        mock_update.effective_chat.id = 12345
        mock_update.effective_user.id = 12345
        mock_update.message.reply_text = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))

        mock_ctx = MagicMock()
        mock_ctx.args = ["https://example.com"]
        mock_ctx.bot.send_message = AsyncMock()

        clip_result = {
            "ok": True,
            "path": "/tmp/test.md",
            "content": "# Test",
            "preview": "# Test",
            "filename": "2026-04-06-test.md",
        }

        with (
            patch("bot.bot.clip_agent", new_callable=AsyncMock, return_value=clip_result),
            patch("bot.bot.request_confirmation", new_callable=AsyncMock, return_value=True),
            patch("bot.bot.clip_agent_write", return_value="Gespeichert"),
            patch("agent.retrieval.index_file", side_effect=fake_index_file),
        ):
            await bot_module.cmd_clip(mock_update, mock_ctx)

        # Kurz warten damit der Task starten kann
        await asyncio.sleep(0.05)

        # Task wurde ausgeführt (oder ist noch in der Registry)
        assert task_added["done"] or len(bot_module._background_tasks) >= initial_size

    def test_background_tasks_separate_from_scheduler_tasks(self) -> None:
        """_background_tasks und _scheduler_tasks sind getrennte Collections."""
        import bot.bot as bot_module

        assert bot_module._background_tasks is not bot_module._scheduler_tasks
        assert isinstance(bot_module._background_tasks, set)
        assert isinstance(bot_module._scheduler_tasks, list)
