"""Tests für agent/telemetry.py – setup_telemetry() Branches."""

from unittest.mock import MagicMock, patch


def _make_settings(tracing="false", api_key="", project="FabBot", endpoint="https://api.smith.langchain.com"):
    settings = MagicMock()
    settings.langchain_tracing_v2 = tracing
    secret = MagicMock()
    secret.get_secret_value.return_value = api_key
    settings.langchain_api_key = secret
    settings.langchain_project = project
    settings.langchain_endpoint = endpoint
    return settings


class TestSetupTelemetry:
    def test_tracing_disabled_no_warning(self, caplog):
        from agent.telemetry import setup_telemetry

        with patch("agent.telemetry.get_settings", return_value=_make_settings(tracing="false")):
            setup_telemetry()

        assert "LANGCHAIN_API_KEY" not in caplog.text

    def test_tracing_not_true_returns_early(self, caplog):
        from agent.telemetry import setup_telemetry

        with patch("agent.telemetry.get_settings", return_value=_make_settings(tracing="  FALSE  ")):
            setup_telemetry()

        assert "LANGCHAIN_API_KEY" not in caplog.text

    def test_tracing_enabled_missing_api_key_warns(self, caplog):
        from agent.telemetry import setup_telemetry

        with patch("agent.telemetry.get_settings", return_value=_make_settings(tracing="true", api_key="")):
            setup_telemetry()

        assert "LANGCHAIN_API_KEY" in caplog.text

    def test_tracing_enabled_with_key_logs_info(self, caplog):
        import logging
        from agent.telemetry import setup_telemetry

        settings = _make_settings(tracing="true", api_key="ls__testkey", project="TestProject")
        with (
            caplog.at_level(logging.INFO, logger="agent.telemetry"),
            patch("agent.telemetry.get_settings", return_value=settings),
            patch.dict("sys.modules", {"langsmith": MagicMock()}),
        ):
            setup_telemetry()

        assert "TestProject" in caplog.text

    def test_tracing_enabled_langsmith_missing_warns(self, caplog):
        from agent.telemetry import setup_telemetry

        settings = _make_settings(tracing="true", api_key="ls__testkey")
        with (
            patch("agent.telemetry.get_settings", return_value=settings),
            patch(
                "builtins.__import__",
                side_effect=lambda name, *a, **kw: (
                    (_ for _ in ()).throw(ImportError()) if name == "langsmith" else __import__(name, *a, **kw)
                ),
            ),
        ):
            setup_telemetry()

        assert "langsmith" in caplog.text.lower()
