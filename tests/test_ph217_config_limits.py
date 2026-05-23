"""
tests/test_ph217_config_limits.py – Phase 217: #208 + #209.

#208: langchain_api_key als SecretStr – wird in Logs/Dumps maskiert.
#209: Media-Limits in agent/config.py – via env überschreibbar.
"""

from pydantic import SecretStr


class TestLangchainApiKeySecretStr:
    def test_field_is_secret_str(self):
        from agent.config import Settings

        settings = Settings()
        assert isinstance(settings.langchain_api_key, SecretStr)

    def test_repr_masks_value(self):
        from agent.config import Settings

        settings = Settings(langchain_api_key="ls__secret123")
        assert "secret123" not in repr(settings.langchain_api_key)
        assert "**" in repr(settings.langchain_api_key)

    def test_get_secret_value_returns_raw(self):
        from agent.config import Settings

        settings = Settings(langchain_api_key="ls__mykey")
        assert settings.langchain_api_key.get_secret_value() == "ls__mykey"

    def test_empty_default(self):
        from agent.config import Settings

        settings = Settings()
        assert settings.langchain_api_key.get_secret_value() == ""


class TestMediaLimitsDefaults:
    def test_image_max_px_default(self):
        from agent.config import Settings

        assert Settings().image_max_px == 1920

    def test_image_max_bytes_default(self):
        from agent.config import Settings

        assert Settings().image_max_bytes == 5_000_000

    def test_pdf_max_bytes_default(self):
        from agent.config import Settings

        assert Settings().pdf_max_bytes == 20_000_000

    def test_pdf_max_chars_default(self):
        from agent.config import Settings

        assert Settings().pdf_max_chars == 100_000

    def test_audio_max_bytes_default(self):
        from agent.config import Settings

        assert Settings().audio_max_bytes == 25_000_000


class TestMediaLimitsEnvOverride:
    def test_image_max_bytes_override(self, monkeypatch):
        from agent.config import get_settings

        monkeypatch.setenv("IMAGE_MAX_BYTES", "1000000")
        get_settings.cache_clear()
        try:
            assert get_settings().image_max_bytes == 1_000_000
        finally:
            get_settings.cache_clear()

    def test_pdf_max_chars_override(self, monkeypatch):
        from agent.config import get_settings

        monkeypatch.setenv("PDF_MAX_CHARS", "50000")
        get_settings.cache_clear()
        try:
            assert get_settings().pdf_max_chars == 50_000
        finally:
            get_settings.cache_clear()

    def test_audio_max_bytes_override(self, monkeypatch):
        from agent.config import get_settings

        monkeypatch.setenv("AUDIO_MAX_BYTES", "10000000")
        get_settings.cache_clear()
        try:
            assert get_settings().audio_max_bytes == 10_000_000
        finally:
            get_settings.cache_clear()


class TestRemovedConstants:
    def test_image_max_bytes_not_in_bot(self):
        import bot.bot as bot_module

        assert not hasattr(bot_module, "_IMAGE_MAX_BYTES")

    def test_image_max_px_not_in_bot(self):
        import bot.bot as bot_module

        assert not hasattr(bot_module, "_IMAGE_MAX_PX")

    def test_pdf_max_bytes_not_in_bot(self):
        import bot.bot as bot_module

        assert not hasattr(bot_module, "_PDF_MAX_BYTES")

    def test_pdf_max_chars_not_in_bot(self):
        import bot.bot as bot_module

        assert not hasattr(bot_module, "_PDF_MAX_CHARS")

    def test_audio_max_bytes_not_in_bot(self):
        import bot.bot as bot_module

        assert not hasattr(bot_module, "_AUDIO_MAX_BYTES")

    def test_max_image_bytes_not_in_vision_agent(self):
        import agent.agents.vision_agent as va_module

        assert not hasattr(va_module, "MAX_IMAGE_BYTES")
