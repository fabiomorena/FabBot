"""
Phase 117 Tests – Issue #45: Screenshot-Analyse in last_agent_result + Memory.

1. computer_agent: last_agent_result enthält Screenshot-Analyse
2. computer_agent: last_agent_name ist 'computer_agent'
3. computer_agent: Proto.SCREENSHOT Prefix in message content
4. _handle_screenshot: _update_memory wird aufgerufen
5. _handle_screenshot: Analyse-Text korrekt extrahiert
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1–3: computer_agent last_agent_result
# ---------------------------------------------------------------------------

class TestComputerAgentScreenshotContext:
    """Phase 117: last_agent_result wird mit Screenshot-Analyse befüllt."""

    def _make_state(self, text: str = "mache einen screenshot") -> dict:
        from langchain_core.messages import HumanMessage
        return {
            "messages": [HumanMessage(content=text)],
            "telegram_chat_id": 12345,
        }

    def _make_llm_patch(self, analysis_text: str):
        """Patcht langchain_anthropic.ChatAnthropic.ainvoke direkt."""
        mock_response = MagicMock()
        mock_response.content = analysis_text
        return patch(
            "langchain_anthropic.ChatAnthropic.ainvoke",
            new_callable=AsyncMock,
            return_value=mock_response,
        )

    @pytest.mark.asyncio
    async def test_screenshot_last_agent_result_not_none(self):
        """last_agent_result ist nicht None nach Screenshot."""
        from agent.agents.computer import computer_agent

        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch("DuckDuckGo Startseite im Chrome Browser."):
            result = await computer_agent(self._make_state())

        assert result["last_agent_result"] is not None
        assert result["last_agent_result"] != ""

    @pytest.mark.asyncio
    async def test_screenshot_last_agent_result_contains_analysis(self):
        """last_agent_result enthält den Analyse-Text."""
        from agent.agents.computer import computer_agent

        analysis_text = "DuckDuckGo Startseite im Chrome Browser."
        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch(analysis_text):
            result = await computer_agent(self._make_state())

        assert result["last_agent_result"] == analysis_text

    @pytest.mark.asyncio
    async def test_screenshot_last_agent_name(self):
        """last_agent_name ist 'computer_agent'."""
        from agent.agents.computer import computer_agent

        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch("Analyse"):
            result = await computer_agent(self._make_state())

        assert result["last_agent_name"] == "computer_agent"

    @pytest.mark.asyncio
    async def test_screenshot_message_has_proto_prefix(self):
        """AIMessage content beginnt mit Proto.SCREENSHOT."""
        from agent.agents.computer import computer_agent
        from agent.protocol import Proto
        from langchain_core.messages import AIMessage

        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch("Analyse"):
            result = await computer_agent(self._make_state())

        ai_msg = result["messages"][0]
        assert isinstance(ai_msg, AIMessage)
        assert ai_msg.content.startswith(Proto.SCREENSHOT)

    @pytest.mark.asyncio
    async def test_screenshot_message_contains_analysis(self):
        """AIMessage content enthält den Analyse-Text nach dem Prefix."""
        from agent.agents.computer import computer_agent
        from agent.protocol import Proto

        analysis_text = "Safari ist geöffnet."
        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch(analysis_text):
            result = await computer_agent(self._make_state())

        content = result["messages"][0].content
        assert content == f"{Proto.SCREENSHOT}{analysis_text}"

    @pytest.mark.asyncio
    async def test_screenshot_fail_returns_error(self):
        """Wenn Screenshot fehlschlägt → last_agent_result enthält Fehlermeldung."""
        from agent.agents.computer import computer_agent

        with patch("agent.agents.computer._take_screenshot", return_value=None):
            result = await computer_agent(self._make_state())

        assert result["last_agent_result"] is not None
        assert "❌" in result["last_agent_result"]

    @pytest.mark.asyncio
    async def test_last_agent_result_analysis_matches_message(self):
        """last_agent_result und message-content enthalten denselben Analyse-Text."""
        from agent.agents.computer import computer_agent
        from agent.protocol import Proto

        analysis_text = "Terminal ist offen."
        with patch("agent.agents.computer._take_screenshot", return_value="base64data"), \
             self._make_llm_patch(analysis_text):
            result = await computer_agent(self._make_state())

        assert result["last_agent_result"] == analysis_text
        assert result["messages"][0].content == f"{Proto.SCREENSHOT}{analysis_text}"


# ---------------------------------------------------------------------------
# 4–5: _handle_screenshot memory update
# ---------------------------------------------------------------------------

class TestHandleScreenshotMemory:
    """Phase 117: _handle_screenshot ruft _update_memory auf."""

    @pytest.mark.asyncio
    async def test_handle_screenshot_calls_update_memory(self):
        """_handle_screenshot schreibt Analyse in Memory."""
        from agent.protocol import Proto

        analysis = "DuckDuckGo Startseite."
        response_msg = f"{Proto.SCREENSHOT}{analysis}"

        mock_bot = AsyncMock()
        mock_screenshot_bytes = b"fakepng"

        with patch("bot.bot._screenshot_to_telegram_bytes", return_value=mock_screenshot_bytes), \
             patch("bot.bot._update_memory", new_callable=AsyncMock) as mock_memory:
            from bot.bot import _handle_screenshot
            await _handle_screenshot(response_msg=response_msg, bot=mock_bot, chat_id=12345)

        mock_memory.assert_called_once()
        call_args = mock_memory.call_args[0]
        assert call_args[0] == 12345
        assert analysis in call_args[1]

    @pytest.mark.asyncio
    async def test_handle_screenshot_memory_contains_analysis(self):
        """Memory-Eintrag enthält den vollständigen Analyse-Text."""
        from agent.protocol import Proto

        analysis = "Safari mit geöffnetem Tab."
        response_msg = f"{Proto.SCREENSHOT}{analysis}"

        mock_bot = AsyncMock()

        with patch("bot.bot._screenshot_to_telegram_bytes", return_value=b"fakepng"), \
             patch("bot.bot._update_memory", new_callable=AsyncMock) as mock_memory:
            from bot.bot import _handle_screenshot
            await _handle_screenshot(response_msg=response_msg, bot=mock_bot, chat_id=99)

        memory_text = mock_memory.call_args[0][1]
        assert analysis in memory_text

    @pytest.mark.asyncio
    async def test_handle_screenshot_no_bytes_still_calls_memory(self):
        """Auch ohne Screenshot-Bytes wird Memory geschrieben."""
        from agent.protocol import Proto

        analysis = "Keine Bytes verfügbar."
        response_msg = f"{Proto.SCREENSHOT}{analysis}"

        mock_bot = AsyncMock()

        with patch("bot.bot._screenshot_to_telegram_bytes", return_value=None), \
             patch("bot.bot._update_memory", new_callable=AsyncMock) as mock_memory:
            from bot.bot import _handle_screenshot
            await _handle_screenshot(response_msg=response_msg, bot=mock_bot, chat_id=42)

        mock_memory.assert_called_once()
