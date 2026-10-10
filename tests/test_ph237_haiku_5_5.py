"""
tests/test_ph237_haiku_5_5.py – Phase 237 (Issue #351)

Haiku-Upgrade auf claude-haiku-5-5. Testet:
- Default-Modell ist claude-haiku-5-5
- get_fast_llm / get_grounding_llm: effort="low", kein temperature (Haiku 5.5 → 400)
- extract_llm_text: thinking-Bloecke (Haiku 5.5 denkt standardmaessig) werden ignoriert
- Overflow-Komprimierung, Entity- und Intent-Extraktion lesen nur den Text-Block
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_HAIKU = "claude-haiku-5-5"

# So liefert langchain-anthropic eine Haiku-5.5-Antwort mit adaptive thinking:
# leerer thinking-Block (display="omitted") vor dem eigentlichen Text.
_THINKING_RESPONSE = [
    {"type": "thinking", "thinking": "", "signature": "sig123"},
    {"type": "text", "text": "Antwort"},
]


def _llm_returning(content) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return llm


# ── Modell-Defaults ───────────────────────────────────────────────────────────


class TestHaikuDefault:
    def test_llm_default_is_haiku_5_5(self):
        from agent.llm import _DEFAULT_HAIKU

        assert _DEFAULT_HAIKU == _HAIKU

    def test_settings_default_is_haiku_5_5(self):
        from agent.config import Settings

        assert Settings.model_fields["anthropic_model_haiku"].default == _HAIKU

    def test_haiku_5_5_passes_startup_validation(self):
        from agent.llm import validate_models_on_startup

        with patch("agent.llm.get_haiku_model", return_value=_HAIKU):
            validate_models_on_startup()


# ── Haiku-Clients ─────────────────────────────────────────────────────────────


class TestHaikuClients:
    def test_fast_llm_uses_low_effort(self):
        import agent.llm as llm_mod

        with patch("agent.llm.get_haiku_model", return_value=_HAIKU):
            llm_mod._fast_llm = None
            llm = llm_mod.get_fast_llm()
        assert llm.effort == "low"

    def test_grounding_llm_uses_low_effort(self):
        from agent.llm import get_grounding_llm

        with patch("agent.llm.get_haiku_model", return_value=_HAIKU):
            llm = get_grounding_llm()
        assert llm.effort == "low"

    def test_grounding_llm_sends_no_temperature(self):
        """Haiku 5.5 lehnt temperature != 1 mit 400 ab – darf nicht im Request landen."""
        from agent.llm import get_grounding_llm

        with patch("agent.llm.get_haiku_model", return_value=_HAIKU):
            llm = get_grounding_llm()
        payload = llm._get_request_payload([("user", "hi")])
        assert "temperature" not in payload
        assert payload["output_config"] == {"effort": "low"}


# ── Content-Extraktion mit thinking-Bloecken ──────────────────────────────────


class TestExtractLlmText:
    def test_ignores_thinking_blocks(self):
        from agent.utils import extract_llm_text

        assert extract_llm_text(_THINKING_RESPONSE) == "Antwort"

    def test_plain_string_unchanged(self):
        from agent.utils import extract_llm_text

        assert extract_llm_text("Antwort") == "Antwort"


class TestCallersReadTextOnly:
    @pytest.mark.asyncio
    async def test_summarize_overflow(self):
        from langchain_core.messages import HumanMessage

        from agent.agents.chat_agent import _summarize_overflow

        with patch("agent.llm.get_fast_llm", return_value=_llm_returning(_THINKING_RESPONSE)):
            result = await _summarize_overflow([HumanMessage(content="Hallo")])
        assert result.content == "[Früherer Kontext]\nAntwort"

    @pytest.mark.asyncio
    async def test_collect_entities(self):
        from agent.proactive import collector

        parse = MagicMock(return_value=[])
        with (
            patch.object(collector, "_get_llm", return_value=_llm_returning(_THINKING_RESPONSE)),
            patch.object(collector, "_parse_entities", parse),
        ):
            await collector.collect_entities("Ich war bei Marco", "Schön")
        parse.assert_called_once_with("Antwort")

    @pytest.mark.asyncio
    async def test_extract_intentions(self):
        from agent.proactive import intent_extractor

        parse = MagicMock(return_value=[])
        with (
            patch.object(intent_extractor, "_get_llm", return_value=_llm_returning(_THINKING_RESPONSE)),
            patch.object(intent_extractor, "_parse_intentions", parse),
        ):
            await intent_extractor.extract_intentions("Ich will morgen joggen gehen")
        parse.assert_called_once_with("Antwort")
