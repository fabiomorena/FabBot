"""
tests/test_ph204_anti_halluzination_pro.py – Phase 204 (Issues #214, #215, #216)

Testet:
- get_grounding_llm: temperature=0 (#215)
- _build_context_word_set: Token-Extraktion
- _mid_sentence_caps: Satzanfang-Filterung
- _has_hallucination: Guard erkennt erfundene Namen, lässt Kontext-Namen durch (#214)
- _extract_named_entities: Whitelist-Extraktion (#216)
- _generate_checkin_question: Guard feuert → Fallback; saubere Antwort → passiert
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── get_grounding_llm (#215) ──────────────────────────────────────────────────


class TestGroundingLlm:
    def test_temperature_is_zero(self):
        """get_grounding_llm() muss temperature=0 liefern."""
        with patch("agent.llm.get_haiku_model", return_value="claude-haiku-4-5-20251001"):
            from agent.llm import get_grounding_llm

            llm = get_grounding_llm()
            assert llm.temperature == 0

    def test_uses_haiku_model(self):
        """get_grounding_llm() nutzt das konfigurierte Haiku-Modell."""
        with patch("agent.llm.get_haiku_model", return_value="claude-haiku-4-5-20251001"):
            from agent.llm import get_grounding_llm

            llm = get_grounding_llm()
            assert "haiku" in llm.model

    def test_not_singleton(self):
        """Jeder Aufruf gibt eine neue Instanz zurück (kein Singleton)."""
        with patch("agent.llm.get_haiku_model", return_value="claude-haiku-4-5-20251001"):
            from agent.llm import get_grounding_llm

            a = get_grounding_llm()
            b = get_grounding_llm()
            assert a is not b


# ── _build_context_word_set ───────────────────────────────────────────────────


class TestBuildContextWordSet:
    def test_lowercases_all_tokens(self):
        from bot.evening_checkin import _build_context_word_set

        result = _build_context_word_set("FabBot Ableton Berlin")
        assert "fabbot" in result
        assert "ableton" in result
        assert "berlin" in result

    def test_excludes_single_char_tokens(self):
        from bot.evening_checkin import _build_context_word_set

        result = _build_context_word_set("a b c ich")
        assert "a" not in result
        assert "b" not in result

    def test_empty_text_returns_empty_set(self):
        from bot.evening_checkin import _build_context_word_set

        assert _build_context_word_set("") == frozenset()


# ── _mid_sentence_caps ────────────────────────────────────────────────────────


class TestMidSentenceCaps:
    def test_ignores_first_word(self):
        from agent.proactive.entity_guard import _mid_sentence_caps

        assert "Wie" not in _mid_sentence_caps("Wie läuft das Projekt?")

    def test_ignores_word_after_sentence_end(self):
        from agent.proactive.entity_guard import _mid_sentence_caps

        result = _mid_sentence_caps("Gut. Morgen geht es weiter.")
        assert "Morgen" not in result

    def test_catches_mid_sentence_name(self):
        from agent.proactive.entity_guard import _mid_sentence_caps

        result = _mid_sentence_caps("Hast du mit Salvador gesprochen?")
        assert "Salvador" in result

    def test_catches_multiple_names(self):
        from agent.proactive.entity_guard import _mid_sentence_caps

        result = _mid_sentence_caps("Ich traf Raven und Salvador heute.")
        assert "Raven" in result
        assert "Salvador" in result


# ── _has_hallucination (#214) ─────────────────────────────────────────────────


class TestHasHallucination:
    def test_detects_invented_name(self):
        """Name nicht im Kontext → True."""
        from bot.evening_checkin import _has_hallucination

        context = frozenset({"fabio", "projekt", "ableton"})
        assert _has_hallucination("Hast du mit Salvador gesprochen?", context) is True

    def test_passes_context_name(self):
        """Name im Kontext → False."""
        from bot.evening_checkin import _has_hallucination

        context = frozenset({"fabio", "anna", "projekt"})
        assert _has_hallucination("Wie lief das Gespräch mit Anna?", context) is False

    def test_ignores_common_german_words(self):
        """Häufige deutsche Wörter aus _COMMON_GERMAN_WORDS werden nicht geflagt."""
        from bot.evening_checkin import _has_hallucination

        context = frozenset({"fabio"})
        assert _has_hallucination("Wie lief deine Arbeit heute?", context) is False

    def test_empty_response_no_hallucination(self):
        from bot.evening_checkin import _has_hallucination

        assert _has_hallucination("", frozenset()) is False

    def test_only_lowercase_response_no_hallucination(self):
        from bot.evening_checkin import _has_hallucination

        assert _has_hallucination("wie war dein tag?", frozenset()) is False

    def test_sentence_start_not_flagged(self):
        """Wörter am Satzanfang nach '.' werden nicht geflagt."""
        from bot.evening_checkin import _has_hallucination

        context = frozenset({"fabio"})
        assert _has_hallucination("Gut gemacht. Morgen weiter.", context) is False


# ── _extract_named_entities (#216) ───────────────────────────────────────────


class TestExtractNamedEntities:
    def test_extracts_proper_names(self):
        from bot.evening_checkin import _extract_named_entities

        result = _extract_named_entities("Fabio hat an Ableton gearbeitet.")
        assert "Ableton" in result

    def test_excludes_common_words(self):
        from bot.evening_checkin import _extract_named_entities

        result = _extract_named_entities("Heute war die Arbeit gut.")
        assert "Heute" not in result
        assert "Arbeit" not in result

    def test_deduplicates(self):
        from bot.evening_checkin import _extract_named_entities

        result = _extract_named_entities("Ableton Ableton Ableton")
        assert result.count("Ableton") == 1

    def test_limits_to_twenty(self):
        from bot.evening_checkin import _extract_named_entities

        many = " ".join(f"Name{i}" for i in range(30))
        result = _extract_named_entities(many)
        assert len(result) <= 20

    def test_empty_text(self):
        from bot.evening_checkin import _extract_named_entities

        assert _extract_named_entities("") == []


# ── _generate_checkin_question – integriert (#214 + #216) ────────────────────


class TestGenerateCheckinQuestionEntityGuard:
    @pytest.mark.asyncio
    async def test_hallucinated_name_triggers_fallback(self):
        """LLM erfindet 'Salvador' → Guard erkennt es → Fallback."""
        from bot.evening_checkin import _generate_checkin_question, _FALLBACK_QUESTION

        mock_response = MagicMock()
        mock_response.content = "Hast du mit Salvador über das Projekt gesprochen?"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        fake_msg = MagicMock()
        fake_msg.type = "human"
        fake_msg.content = "Ich hab heute an Ableton gearbeitet."

        with (
            patch("bot.session_summary._get_messages_from_state", new=AsyncMock(return_value=[fake_msg])),
            patch("bot.session_summary._filter_messages", return_value=[fake_msg]),
            patch("bot.evening_checkin._filter_checkin_context", return_value=[fake_msg]),
            patch(
                "bot.session_summary._format_for_summary",
                return_value="User: Ich hab heute an Ableton gearbeitet.",
            ),
            patch("agent.llm.get_grounding_llm", return_value=mock_llm),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == _FALLBACK_QUESTION

    @pytest.mark.asyncio
    async def test_clean_response_passes_guard(self):
        """LLM nennt nur Kontext-Begriffe → Guard lässt durch."""
        from bot.evening_checkin import _generate_checkin_question

        mock_response = MagicMock()
        mock_response.content = "Wie lief deine Session in Ableton heute?"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        fake_msg = MagicMock()
        fake_msg.type = "human"
        fake_msg.content = "Ich hab heute an Ableton gearbeitet."

        with (
            patch("bot.session_summary._get_messages_from_state", new=AsyncMock(return_value=[fake_msg])),
            patch("bot.session_summary._filter_messages", return_value=[fake_msg]),
            patch("bot.evening_checkin._filter_checkin_context", return_value=[fake_msg]),
            patch(
                "bot.session_summary._format_for_summary",
                return_value="User: Ich hab heute an Ableton gearbeitet.",
            ),
            patch("agent.llm.get_grounding_llm", return_value=mock_llm),
        ):
            result = await _generate_checkin_question(chat_id=123)
            assert result == "Wie lief deine Session in Ableton heute?"
