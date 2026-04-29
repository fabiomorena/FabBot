"""
Phase 92 Tests:
1. crypto.py – keyring Fehlerbehandlung (RuntimeError mit klarer Meldung)
2. audit.py  – setup_audit_logger() Idempotenz + Isolation
3. llm.py    – _warn_if_unusual() Warning-Log
4. .env.example – enthält alle wichtigen Variablen
"""

import logging
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# 1. crypto.py – keyring Fehlerbehandlung
# ---------------------------------------------------------------------------


class TestCryptoKeyringErrorHandling:
    """Phase 92: _get_fernet() wirft RuntimeError mit klarer Meldung."""

    def setup_method(self) -> None:
        """Fernet-Singleton vor jedem Test zurücksetzen."""
        import agent.crypto as crypto_module

        crypto_module._fernet = None

    def teardown_method(self) -> None:
        import agent.crypto as crypto_module

        crypto_module._fernet = None

    def test_keyring_get_error_raises_runtime_error(self) -> None:
        """keyring.get_password() wirft → RuntimeError mit Hinweis auf Keychain."""
        from agent.crypto import _get_fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        with patch("keyring.get_password", side_effect=Exception("DBusException")):
            with pytest.raises(RuntimeError) as exc_info:
                _get_fernet()

        assert "Keychain" in str(exc_info.value) or "keychain" in str(exc_info.value).lower()
        assert "DBusException" in str(exc_info.value)

    def test_keyring_set_error_raises_runtime_error(self) -> None:
        """keyring.set_password() wirft → RuntimeError beim Speichern des Keys."""
        from agent.crypto import _get_fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        with (
            patch("keyring.get_password", return_value=None),
            patch("keyring.set_password", side_effect=Exception("NoKeyringError")),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                _get_fernet()

        assert "gespeichert" in str(exc_info.value) or "Keychain" in str(exc_info.value)
        assert "NoKeyringError" in str(exc_info.value)

    def test_runtime_error_has_cause(self) -> None:
        """RuntimeError hat __cause__ (from e) gesetzt."""
        from agent.crypto import _get_fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        original = Exception("original error")
        with patch("keyring.get_password", side_effect=original):
            with pytest.raises(RuntimeError) as exc_info:
                _get_fernet()

        assert exc_info.value.__cause__ is original

    def test_no_exception_on_success(self) -> None:
        """Kein Fehler bei erfolgreichem Keychain-Zugriff."""
        from agent.crypto import _get_fernet
        from cryptography.fernet import Fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        key = Fernet.generate_key()
        with patch("keyring.get_password", return_value=key.decode()):
            fernet = _get_fernet()

        assert fernet is not None

    def test_singleton_cached_after_success(self) -> None:
        """Nach erfolgreichem Laden wird Singleton gecacht."""
        from agent.crypto import _get_fernet
        from cryptography.fernet import Fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        key = Fernet.generate_key()
        with patch("keyring.get_password", return_value=key.decode()) as mock_get:
            _get_fernet()
            _get_fernet()  # zweiter Aufruf

        assert mock_get.call_count == 1  # nur einmal Keychain gelesen

    def test_encrypt_propagates_runtime_error(self) -> None:
        """encrypt() propagiert RuntimeError wenn Keychain fehlt."""
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        with patch("keyring.get_password", side_effect=Exception("no keychain")):
            with pytest.raises(RuntimeError):
                from agent.crypto import encrypt

                encrypt("test")

    def test_error_message_mentions_macos(self) -> None:
        """Fehlermeldung enthält Hinweis auf macOS oder kompatiblen Secret Store."""
        from agent.crypto import _get_fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        with patch("keyring.get_password", side_effect=Exception("test")):
            with pytest.raises(RuntimeError) as exc_info:
                _get_fernet()

        msg = str(exc_info.value)
        assert "macOS" in msg or "Keyring" in msg or "Secret Store" in msg

    def test_new_key_generated_when_none_in_keychain(self) -> None:
        """Wenn kein Key im Keychain → neuer Key wird generiert und gespeichert."""
        from agent.crypto import _get_fernet
        import agent.crypto as crypto_module

        crypto_module._fernet = None

        with patch("keyring.get_password", return_value=None), patch("keyring.set_password") as mock_set:
            _get_fernet()

        mock_set.assert_called_once()
        service, username, key_str = mock_set.call_args[0]
        assert service == "fabbot"
        assert username == "profile_key"
        assert len(key_str) > 30  # Fernet-Keys sind Base64, ~44 Zeichen


# ---------------------------------------------------------------------------
# 2. audit.py – setup_audit_logger() Idempotenz + Isolation
# ---------------------------------------------------------------------------


class TestAuditSetupLogger:
    """Phase 92: setup_audit_logger() ersetzt Module-Level-Seiteneffekte."""

    def setup_method(self) -> None:
        """Audit-State vor jedem Test zurücksetzen."""
        import agent.audit as audit_module

        # Alle Handler entfernen
        audit_module.audit_logger.handlers.clear()
        audit_module._audit_initialized = False

    def teardown_method(self) -> None:
        import agent.audit as audit_module

        audit_module.audit_logger.handlers.clear()
        audit_module._audit_initialized = False

    def test_setup_audit_logger_exists(self) -> None:
        """setup_audit_logger ist eine callable Funktion."""
        from agent.audit import setup_audit_logger

        assert callable(setup_audit_logger)

    def test_no_filehandler_on_import(self) -> None:
        """Beim Import von agent.audit wird kein FileHandler geöffnet."""
        import agent.audit as audit_module

        # Nach setup_method sind alle Handler entfernt und Flag False
        # → kein Handler sollte vorhanden sein
        assert len(audit_module.audit_logger.handlers) == 0

    def test_setup_adds_handler(self, tmp_path) -> None:
        """setup_audit_logger() fügt einen FileHandler hinzu."""
        import agent.audit as audit_module
        from agent.audit import setup_audit_logger

        log_path = tmp_path / "audit.log"
        with patch("agent.audit.AUDIT_LOG_PATH", log_path):
            setup_audit_logger()

        assert len(audit_module.audit_logger.handlers) == 1
        assert isinstance(audit_module.audit_logger.handlers[0], logging.FileHandler)

    def test_setup_idempotent(self, tmp_path) -> None:
        """Mehrfache Aufrufe von setup_audit_logger() fügen keinen zweiten Handler hinzu."""
        import agent.audit as audit_module
        from agent.audit import setup_audit_logger

        log_path = tmp_path / "audit.log"
        with patch("agent.audit.AUDIT_LOG_PATH", log_path):
            setup_audit_logger()
            setup_audit_logger()
            setup_audit_logger()

        assert len(audit_module.audit_logger.handlers) == 1

    def test_initialized_flag_set(self, tmp_path) -> None:
        """_audit_initialized wird nach setup auf True gesetzt."""
        import agent.audit as audit_module
        from agent.audit import setup_audit_logger

        assert audit_module._audit_initialized is False
        log_path = tmp_path / "audit.log"
        with patch("agent.audit.AUDIT_LOG_PATH", log_path):
            setup_audit_logger()

        assert audit_module._audit_initialized is True

    def test_log_action_works_without_setup(self) -> None:
        """log_action() crasht nicht wenn setup_audit_logger() noch nicht aufgerufen wurde."""
        from agent.audit import log_action

        # Kein Handler → propagate=False → Message wird verworfen – kein Crash
        log_action("test", "action", "detail", 123, status="executed")

    def test_setup_creates_directory(self, tmp_path) -> None:
        """setup_audit_logger() legt das Verzeichnis an wenn es nicht existiert."""
        from agent.audit import setup_audit_logger

        log_path = tmp_path / "subdir" / "audit.log"
        with patch("agent.audit.AUDIT_LOG_PATH", log_path):
            setup_audit_logger()

        assert log_path.parent.exists()

    def test_audit_logger_propagate_false(self) -> None:
        """audit_logger.propagate ist False – keine doppelten Logs."""
        from agent.audit import audit_logger

        assert audit_logger.propagate is False


# ---------------------------------------------------------------------------
# 3. llm.py – _warn_if_unusual() Warning-Log
# ---------------------------------------------------------------------------


class TestLlmModelValidation:
    """Phase 92: _warn_if_unusual() loggt Warning bei ungewöhnlichem Modell-String."""

    def setup_method(self) -> None:
        import agent.llm as llm_module

        llm_module._llm = None
        llm_module._fast_llm = None

    def teardown_method(self) -> None:
        import agent.llm as llm_module

        llm_module._llm = None
        llm_module._fast_llm = None

    def test_warn_if_unusual_exists(self) -> None:
        """_warn_if_unusual ist eine callable Funktion."""
        from agent.llm import _warn_if_unusual

        assert callable(_warn_if_unusual)

    def test_valid_sonnet_model_no_warning(self, caplog) -> None:
        """Gültiger Sonnet-String mit Datum → keine Warning."""
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("claude-sonnet-4-20250514")
        assert not any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_valid_haiku_model_no_warning(self, caplog) -> None:
        """Gültiger Haiku-String → keine Warning."""
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("claude-haiku-4-5-20251001")
        assert not any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_valid_sonnet_model_without_date_no_warning(self, caplog) -> None:
        """Phase 116: Sonnet/Opus ohne Datumssuffix ist valide → keine Warning.
        Neue Modelle wie claude-sonnet-4-6, claude-opus-4-7 haben kein YYYYMMDD-Suffix.
        """
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("claude-sonnet-4-6")
            _warn_if_unusual("claude-opus-4-7")
        assert not any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_typo_triggers_warning(self, caplog) -> None:
        """Tippfehler wie 'claud-sonnet-4-20250514' → Warning."""
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("claud-sonnet-4-20250514")
        assert any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_empty_model_triggers_warning(self, caplog) -> None:
        """Leerer String → Warning."""
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("")
        assert any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_missing_claude_prefix_triggers_warning(self, caplog) -> None:
        """Phase 116: String ohne 'claude-' Prefix → Warning.
        (Ersetzt test_missing_date_triggers_warning: Datumssuffix ist seit Phase 116 optional.)
        """
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("claud-sonnet")
        assert any("Ungewöhnlicher" in r.message for r in caplog.records)

    def test_warning_contains_model_string(self, caplog) -> None:
        """Warning-Meldung enthält den fehlerhaften Modell-String."""
        from agent.llm import _warn_if_unusual

        with caplog.at_level(logging.WARNING, logger="agent.llm"):
            _warn_if_unusual("wrong-model-xyz")
        assert any("wrong-model-xyz" in r.message for r in caplog.records)

    def test_model_pattern_constant_exists(self) -> None:
        """_MODEL_PATTERN ist ein kompilierter Regex."""
        import re
        from agent.llm import _MODEL_PATTERN

        assert isinstance(_MODEL_PATTERN, type(re.compile("")))

    def test_get_llm_warns_on_bad_model(self, caplog) -> None:
        """get_llm() loggt Warning wenn Modell-String ungewöhnlich ist."""
        import agent.llm as llm_module

        llm_module._llm = None

        with (
            patch.dict("os.environ", {"ANTHROPIC_MODEL_SONNET": "bad-model"}),
            caplog.at_level(logging.WARNING, logger="agent.llm"),
        ):
            with patch("langchain_anthropic.ChatAnthropic"):
                llm_module.get_llm()

        assert any("Ungewöhnlicher" in r.message or "bad-model" in r.message for r in caplog.records)

    def test_get_fast_llm_warns_on_bad_model(self, caplog) -> None:
        """get_fast_llm() loggt Warning wenn Modell-String ungewöhnlich ist."""
        import agent.llm as llm_module

        llm_module._fast_llm = None

        with (
            patch.dict("os.environ", {"ANTHROPIC_MODEL_HAIKU": "bad-haiku"}),
            caplog.at_level(logging.WARNING, logger="agent.llm"),
        ):
            with patch("langchain_anthropic.ChatAnthropic"):
                llm_module.get_fast_llm()

        assert any("Ungewöhnlicher" in r.message or "bad-haiku" in r.message for r in caplog.records)

    def test_valid_model_no_reinitialization_warning(self, caplog) -> None:
        """Gültiges Modell → beim Singleton-Reuse keine Warning."""
        import agent.llm as llm_module

        llm_module._llm = None
        model = "claude-sonnet-4-20250514"

        with (
            patch.dict("os.environ", {"ANTHROPIC_MODEL_SONNET": model}),
            patch("langchain_anthropic.ChatAnthropic") as MockLLM,
            caplog.at_level(logging.WARNING, logger="agent.llm"),
        ):
            mock_instance = MagicMock()
            mock_instance.model = model
            MockLLM.return_value = mock_instance
            llm_module.get_llm()
            caplog.clear()
            llm_module.get_llm()  # Singleton-Reuse

        assert not any("Ungewöhnlicher" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. .env.example – enthält alle wichtigen Variablen
# ---------------------------------------------------------------------------


class TestEnvExample:
    """Phase 92: .env.example enthält alle dokumentierten Variablen."""

    @pytest.fixture
    def env_content(self) -> str:
        """Lädt .env.example aus dem Projekt-Root."""
        from pathlib import Path

        env_path = Path(__file__).parent.parent / ".env.example"
        if not env_path.exists():
            pytest.skip(".env.example nicht gefunden")
        return env_path.read_text(encoding="utf-8")

    def test_telegram_bot_token_present(self, env_content: str) -> None:
        assert "TELEGRAM_BOT_TOKEN" in env_content

    def test_telegram_allowed_user_ids_present(self, env_content: str) -> None:
        assert "TELEGRAM_ALLOWED_USER_IDS" in env_content

    def test_anthropic_api_key_present(self, env_content: str) -> None:
        assert "ANTHROPIC_API_KEY" in env_content

    def test_telegram_chat_id_present(self, env_content: str) -> None:
        """TELEGRAM_CHAT_ID muss dokumentiert sein (auch auskommentiert)."""
        assert "TELEGRAM_CHAT_ID" in env_content

    def test_openai_api_key_present(self, env_content: str) -> None:
        """OPENAI_API_KEY für Retrieval + TTS muss dokumentiert sein."""
        assert "OPENAI_API_KEY" in env_content

    def test_langchain_api_key_present(self, env_content: str) -> None:
        """LangSmith-Key muss dokumentiert sein."""
        assert "LANGCHAIN_API_KEY" in env_content

    def test_langchain_tracing_present(self, env_content: str) -> None:
        assert "LANGCHAIN_TRACING_V2" in env_content

    def test_openai_tts_voice_present(self, env_content: str) -> None:
        assert "OPENAI_TTS_VOICE" in env_content

    def test_tavily_api_key_present(self, env_content: str) -> None:
        assert "TAVILY_API_KEY" in env_content

    def test_anthropic_model_sonnet_present(self, env_content: str) -> None:
        assert "ANTHROPIC_MODEL_SONNET" in env_content

    def test_no_real_secrets_in_file(self, env_content: str) -> None:
        """Keine echten Keys in .env.example."""
        import re

        assert not re.search(r"sk-ant-api03-[A-Za-z0-9]", env_content)
        assert not re.search(r"sk-[A-Za-z0-9]{20,}", env_content)
        assert not re.search(r"ls__[A-Za-z0-9]{20,}", env_content)
