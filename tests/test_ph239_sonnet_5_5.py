"""
tests/test_ph239_sonnet_5_5.py – Phase 239 (Issue #357)

Sonnet-Upgrade auf claude-sonnet-5-5. Testet:
- Default-Modell ist claude-sonnet-5-5
- get_llm: effort="low", max_tokens explizit (langchain-anthropic kennt kein Profil
  fuer sonnet-5-5 und faellt sonst auf 4096 zurueck), kein temperature
"""

from unittest.mock import patch

_SONNET = "claude-sonnet-5-5"


def _fresh_llm():
    import agent.llm as llm_mod

    with patch("agent.llm.get_sonnet_model", return_value=_SONNET):
        llm_mod._llm = None
        return llm_mod.get_llm()


class TestSonnetDefault:
    def test_llm_default_is_sonnet_5_5(self):
        from agent.llm import _DEFAULT_SONNET

        assert _DEFAULT_SONNET == _SONNET

    def test_settings_default_is_sonnet_5_5(self):
        from agent.config import Settings

        assert Settings.model_fields["anthropic_model_sonnet"].default == _SONNET

    def test_sonnet_5_5_passes_startup_validation(self):
        from agent.llm import validate_models_on_startup

        with patch("agent.llm.get_sonnet_model", return_value=_SONNET):
            validate_models_on_startup()


class TestSonnetClient:
    def test_uses_low_effort(self):
        assert _fresh_llm().effort == "low"

    def test_max_tokens_explicit(self):
        """Ohne explizites max_tokens faellt langchain-anthropic auf 4096 zurueck."""
        assert _fresh_llm().max_tokens == 16000

    def test_payload(self):
        payload = _fresh_llm()._get_request_payload([("user", "hi")])
        assert payload["model"] == _SONNET
        assert payload["max_tokens"] == 16000
        assert payload["output_config"] == {"effort": "low"}
        assert "temperature" not in payload
        assert "thinking" not in payload
