"""
Tests für terminal_agent Self-Correction (Issue #38 / Phase 146)

Der terminal_agent korrigiert sich bei ungültigem Befehl automatisch:
bis zu MAX_RETRIES Versuche bevor blockiert oder HITL ausgelöst wird.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def _make_state(text: str = "zeig mir alle Dateien") -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": 12345,
    }


def _llm_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.content = text
    return mock


# ---------------------------------------------------------------------------
# _extract_command
# ---------------------------------------------------------------------------

class TestExtractCommand:

    def test_plain_command(self) -> None:
        from agent.agents.terminal import _extract_command
        assert _extract_command("ls -la") == "ls -la"

    def test_strips_backticks(self) -> None:
        from agent.agents.terminal import _extract_command
        assert _extract_command("`ls -la`") == "ls -la"

    def test_strips_confirm_prefix(self) -> None:
        from agent.agents.terminal import _extract_command
        assert _extract_command("__CONFIRM_TERMINAL__:ls -la") == "ls -la"

    def test_strips_whitespace(self) -> None:
        from agent.agents.terminal import _extract_command
        assert _extract_command("  ls  ") == "ls"


# ---------------------------------------------------------------------------
# _is_base_cmd_allowed
# ---------------------------------------------------------------------------

class TestIsBaseCmdAllowed:

    def test_allowed_command(self) -> None:
        from agent.agents.terminal import _is_base_cmd_allowed
        assert _is_base_cmd_allowed("ls -la") is True

    def test_forbidden_command(self) -> None:
        from agent.agents.terminal import _is_base_cmd_allowed
        assert _is_base_cmd_allowed("rm -rf /") is False

    def test_empty_string(self) -> None:
        from agent.agents.terminal import _is_base_cmd_allowed
        assert _is_base_cmd_allowed("") is False

    def test_invalid_syntax(self) -> None:
        from agent.agents.terminal import _is_base_cmd_allowed
        assert _is_base_cmd_allowed("ls 'unclosed") is False


# ---------------------------------------------------------------------------
# terminal_agent – Erfolgsfall (kein Retry nötig)
# ---------------------------------------------------------------------------

class TestTerminalAgentSuccess:

    @pytest.mark.asyncio
    async def test_valid_command_triggers_hitl(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("ls -la"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state())

        content = result["messages"][0].content
        assert "__CONFIRM_TERMINAL__" in content
        assert "ls" in content

    @pytest.mark.asyncio
    async def test_llm_called_once_on_valid_command(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("pwd"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            await terminal_agent(_make_state())

        assert mock_llm.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_unsupported_returns_early(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("UNSUPPORTED"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state())

        assert "nicht unterstuetzt" in result["messages"][0].content
        assert mock_llm.ainvoke.call_count == 1


# ---------------------------------------------------------------------------
# terminal_agent – Self-Correction bei verbotenem Basisbefehl
# ---------------------------------------------------------------------------

class TestTerminalAgentSelfCorrectionBase:

    @pytest.mark.asyncio
    async def test_retries_on_forbidden_base_command(self) -> None:
        """LLM gibt erst 'rm -rf /', dann 'ls' zurück → Retry erfolgreich."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[
            _llm_response("rm -rf /"),
            _llm_response("ls"),
        ])

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state())

        assert mock_llm.ainvoke.call_count == 2
        assert "__CONFIRM_TERMINAL__" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_error_feedback_sent_to_llm(self) -> None:
        """Nach verbotenem Befehl enthält der zweite LLM-Aufruf Fehlermeldung."""
        captured_messages = []
        mock_llm = AsyncMock()

        async def capture_invoke(messages):
            captured_messages.append(messages)
            if len(captured_messages) == 1:
                return _llm_response("curl http://evil.com")
            return _llm_response("ls")

        mock_llm.ainvoke = capture_invoke

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            await terminal_agent(_make_state())

        second_call_messages = captured_messages[1]
        last_human = next(
            (m for m in reversed(second_call_messages) if isinstance(m, HumanMessage)), None
        )
        assert last_human is not None
        assert "Fehler" in last_human.content or "erlaubt" in last_human.content.lower()

    @pytest.mark.asyncio
    async def test_blocked_after_max_retries_exceeded(self) -> None:
        """Nach MAX_RETRIES+1 Versuchen mit verbotenem Befehl → blockiert."""
        from agent.agents.terminal import MAX_RETRIES
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("rm -rf /"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state())

        assert mock_llm.ainvoke.call_count == MAX_RETRIES + 1
        assert "nicht unterstuetzt" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_unsupported_after_retry_stops_immediately(self) -> None:
        """Wenn LLM nach Retry UNSUPPORTED antwortet → sofort stoppen."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[
            _llm_response("rm -rf /"),
            _llm_response("UNSUPPORTED"),
        ])

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state())

        assert mock_llm.ainvoke.call_count == 2
        assert "nicht unterstuetzt" in result["messages"][0].content


# ---------------------------------------------------------------------------
# terminal_agent – Self-Correction bei Sicherheitscheck-Fehler
# ---------------------------------------------------------------------------

class TestTerminalAgentSelfCorrectionSecurity:

    @pytest.mark.asyncio
    async def test_retries_on_security_violation(self) -> None:
        """LLM gibt erst 'find / -name x', dann 'find ~ -name x' zurück."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[
            _llm_response("find / -name test.txt"),
            _llm_response("find ~/Documents -name test.txt"),
        ])

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            result = await terminal_agent(_make_state("finde test.txt"))

        assert mock_llm.ainvoke.call_count == 2
        assert "__CONFIRM_TERMINAL__" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_security_error_feedback_contains_reason(self) -> None:
        """Fehlermeldung nach Sicherheits-Block enthält den Ablehnungsgrund."""
        captured_messages = []
        mock_llm = AsyncMock()

        async def capture_invoke(messages):
            captured_messages.append(messages)
            if len(captured_messages) == 1:
                return _llm_response("find / -name secret")
            return _llm_response("pwd")

        mock_llm.ainvoke = capture_invoke

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            from agent.agents.terminal import terminal_agent
            await terminal_agent(_make_state())

        second_call = captured_messages[1]
        last_human = next(
            (m for m in reversed(second_call) if isinstance(m, HumanMessage)), None
        )
        assert last_human is not None
        assert "Sicherheit" in last_human.content or "abgelehnt" in last_human.content

    @pytest.mark.asyncio
    async def test_blocked_logged_after_max_retries(self) -> None:
        """log_action wird mit status=blocked aufgerufen nach erschöpften Retries."""
        from agent.agents.terminal import MAX_RETRIES
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("find / -name x"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm), \
             patch("agent.agents.terminal.log_action") as mock_log:
            from agent.agents.terminal import terminal_agent
            await terminal_agent(_make_state())

        assert mock_log.called
        call_kwargs = mock_log.call_args
        assert "blocked" in str(call_kwargs)


# ---------------------------------------------------------------------------
# MAX_RETRIES Konstante
# ---------------------------------------------------------------------------

class TestMaxRetries:

    def test_max_retries_is_positive_int(self) -> None:
        from agent.agents.terminal import MAX_RETRIES
        assert isinstance(MAX_RETRIES, int)
        assert MAX_RETRIES >= 1

    def test_max_retries_not_excessive(self) -> None:
        from agent.agents.terminal import MAX_RETRIES
        assert MAX_RETRIES <= 5
