"""
tests/test_ph216_context_window.py – Phase 216 (Issues #231, #232)

Testet Anchor-Messages + Inline-Komprimierung im Context-Window:
- _apply_context_window: <= Limit → unverändert, > Limit → anchor + summary + recent
- _summarize_overflow: normaler Fall, leere Liste, LLM-Timeout-Fallback
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class TestApplyContextWindow:
    @pytest.mark.asyncio
    async def test_no_trim_when_within_limit(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(5)]
        result = await _apply_context_window(msgs, context_window=10)
        assert result == msgs

    @pytest.mark.asyncio
    async def test_exact_limit_unchanged(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(10)]
        result = await _apply_context_window(msgs, context_window=10)
        assert result == msgs

    @pytest.mark.asyncio
    async def test_anchor_preserved(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(25)]
        with patch("agent.agents.chat_agent._summarize_overflow", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = SystemMessage(content="[Summary]")
            result = await _apply_context_window(msgs, context_window=20)

        assert result[0] == msgs[0]  # erste Nachricht erhalten

    @pytest.mark.asyncio
    async def test_recent_messages_preserved(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(25)]
        with patch("agent.agents.chat_agent._summarize_overflow", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = SystemMessage(content="[Summary]")
            result = await _apply_context_window(msgs, context_window=20)

        # Die letzten 19 Nachrichten müssen enthalten sein (context_window - 1)
        assert msgs[-1] in result
        assert msgs[-19] in result

    @pytest.mark.asyncio
    async def test_summary_injected_for_overflow(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(25)]
        summary = SystemMessage(content="[Früherer Kontext]\nZusammenfassung")
        with patch("agent.agents.chat_agent._summarize_overflow", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = summary
            result = await _apply_context_window(msgs, context_window=20)

        assert summary in result
        mock_sum.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_summary_when_no_overflow(self):
        """Wenn anchor + recent genau context_window füllen, kein Summary nötig."""
        from agent.agents.chat_agent import _apply_context_window

        # 20 Messages, context_window=20 → kein Trim
        msgs = [HumanMessage(content=f"msg{i}") for i in range(20)]
        with patch("agent.agents.chat_agent._summarize_overflow", new_callable=AsyncMock) as mock_sum:
            result = await _apply_context_window(msgs, context_window=20)
        mock_sum.assert_not_awaited()
        assert result == msgs

    @pytest.mark.asyncio
    async def test_overflow_messages_passed_to_summarize(self):
        from agent.agents.chat_agent import _apply_context_window

        msgs = [HumanMessage(content=f"msg{i}") for i in range(25)]
        with patch("agent.agents.chat_agent._summarize_overflow", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = SystemMessage(content="[Summary]")
            await _apply_context_window(msgs, context_window=20)

        overflow_passed = mock_sum.call_args[0][0]
        # Overflow = messages[1 : 25-19] = messages[1:6] = 5 messages
        assert len(overflow_passed) == 5
        assert overflow_passed[0] == msgs[1]
        assert overflow_passed[-1] == msgs[5]


class TestSummarizeOverflow:
    @pytest.mark.asyncio
    async def test_returns_system_message(self):
        from agent.agents.chat_agent import _summarize_overflow

        msgs = [HumanMessage(content="Hallo"), AIMessage(content="Hi")]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Kurze Zusammenfassung.")

        with patch("agent.llm.get_fast_llm", return_value=mock_llm):
            result = await _summarize_overflow(msgs)

        assert isinstance(result, SystemMessage)
        assert "Früherer Kontext" in result.content

    @pytest.mark.asyncio
    async def test_empty_messages_returns_fallback(self):
        from agent.agents.chat_agent import _summarize_overflow

        result = await _summarize_overflow([])
        assert isinstance(result, SystemMessage)
        assert "keine Details" in result.content

    @pytest.mark.asyncio
    async def test_llm_error_returns_text_fallback(self):
        from agent.agents.chat_agent import _summarize_overflow

        msgs = [HumanMessage(content="Test"), AIMessage(content="Antwort")]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = Exception("LLM down")

        with patch("agent.llm.get_fast_llm", return_value=mock_llm):
            result = await _summarize_overflow(msgs)

        assert isinstance(result, SystemMessage)
        assert "Früherer Kontext" in result.content
        assert "Test" in result.content  # Fallback enthält den Originaltext

    @pytest.mark.asyncio
    async def test_only_human_and_ai_messages_included(self):
        from agent.agents.chat_agent import _summarize_overflow

        msgs = [
            SystemMessage(content="System-Msg wird ignoriert"),
            HumanMessage(content="User-Frage"),
            AIMessage(content="Bot-Antwort"),
        ]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = MagicMock(content="Summary.")

        with patch("agent.llm.get_fast_llm", return_value=mock_llm):
            await _summarize_overflow(msgs)

        prompt = mock_llm.ainvoke.call_args[0][0][0].content
        assert "User-Frage" in prompt
        assert "Bot-Antwort" in prompt
        assert "System-Msg" not in prompt
