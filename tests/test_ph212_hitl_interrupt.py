"""
Phase 212 (Issue #129): HITL via LangGraph interrupt().

Verifiziert dass terminal_agent + file_agent statt Magic-String-Returns
einen Pregel-Interrupt auslösen, der mit Command(resume=...) fortgesetzt wird.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command


def _llm_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.content = text
    return mock


def _build_single_node_graph(node_fn, state_type):
    """Minimal-Graph mit einem Node – für Interrupt-Tests."""
    g = StateGraph(state_type)
    g.add_node("agent", node_fn)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=MemorySaver())


class TestTerminalAgentInterrupt:
    @pytest.mark.asyncio
    async def test_emits_interrupt_with_command(self) -> None:
        from agent.agents.terminal import terminal_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("ls -la"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            app = _build_single_node_graph(terminal_agent, AgentState)
            config = {"configurable": {"thread_id": "t1"}}
            result = await app.ainvoke(
                {"messages": [HumanMessage(content="zeig dateien")], "telegram_chat_id": 42},
                config,
            )

        interrupts = result.get("__interrupt__")
        assert interrupts is not None
        assert len(interrupts) == 1
        value = interrupts[0].value
        assert value["type"] == "terminal"
        assert value["command"].startswith("ls")

    @pytest.mark.asyncio
    async def test_resume_with_confirmed_executes(self, tmp_path: Path) -> None:
        from agent.agents.terminal import terminal_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("pwd"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            app = _build_single_node_graph(terminal_agent, AgentState)
            config = {"configurable": {"thread_id": "t-confirmed"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="wo bin ich")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(resume={"confirmed": True, "rate_limit_ok": True, "command": "pwd"}),
                config,
            )

        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs, "Es sollte eine AIMessage mit Output geben"
        assert "Output:" in ai_msgs[-1].content

    @pytest.mark.asyncio
    async def test_resume_with_rejected_returns_abbruch(self) -> None:
        from agent.agents.terminal import terminal_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("ls"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            app = _build_single_node_graph(terminal_agent, AgentState)
            config = {"configurable": {"thread_id": "t-rejected"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="zeig")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(resume={"confirmed": False}),
                config,
            )

        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs
        assert "abgebrochen" in ai_msgs[-1].content.lower()

    @pytest.mark.asyncio
    async def test_resume_with_rate_limit_blocked(self) -> None:
        from agent.agents.terminal import terminal_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response("ls"))

        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            app = _build_single_node_graph(terminal_agent, AgentState)
            config = {"configurable": {"thread_id": "t-ratelimit"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="zeig")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(resume={"confirmed": True, "rate_limit_ok": False, "command": "ls"}),
                config,
            )

        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs
        assert "Rate Limit" in ai_msgs[-1].content


class TestFileAgentInterrupt:
    @pytest.mark.asyncio
    async def test_write_emits_interrupt(self, tmp_path: Path) -> None:
        from agent.agents.file import file_agent
        from agent.state import AgentState

        target = tmp_path / "out.txt"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_llm_response(f'{{"action": "write", "path": "{target}", "content": "hallo"}}')
        )

        with (
            patch("agent.agents.file.get_llm", return_value=mock_llm),
            patch("agent.agents.file.is_path_allowed", return_value=(True, str(target))),
        ):
            app = _build_single_node_graph(file_agent, AgentState)
            config = {"configurable": {"thread_id": "f1"}}
            result = await app.ainvoke(
                {"messages": [HumanMessage(content="schreib hallo")], "telegram_chat_id": 42},
                config,
            )

        interrupts = result.get("__interrupt__")
        assert interrupts is not None
        value = interrupts[0].value
        assert value["type"] == "file_write"
        assert value["content"] == "hallo"
        assert str(target) in value["path"]

    @pytest.mark.asyncio
    async def test_resume_with_confirmed_writes_file(self, tmp_path: Path) -> None:
        from agent.agents.file import file_agent
        from agent.state import AgentState

        target = tmp_path / "out.txt"
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_llm_response(f'{{"action": "write", "path": "{target}", "content": "hallo"}}')
        )

        with (
            patch("agent.agents.file.get_llm", return_value=mock_llm),
            patch("agent.agents.file.is_path_allowed", return_value=(True, str(target))),
        ):
            app = _build_single_node_graph(file_agent, AgentState)
            config = {"configurable": {"thread_id": "f-confirmed"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="schreib")], "telegram_chat_id": 42},
                config,
            )
            await app.ainvoke(
                Command(
                    resume={
                        "confirmed": True,
                        "rate_limit_ok": True,
                        "path": str(target),
                        "content": "hallo",
                    }
                ),
                config,
            )

        assert target.exists()
        assert target.read_text() == "hallo"


class TestBotHandleInterrupt:
    """Verifiziert _handle_interrupt() in bot.py – Brücke zwischen Pregel-Interrupt
    und Telegram-Confirmation-UI."""

    @pytest.mark.asyncio
    async def test_terminal_confirmed_passes_command(self) -> None:
        from bot import bot as bot_mod

        with (
            patch.object(bot_mod, "request_confirmation", AsyncMock(return_value=True)),
            patch.object(bot_mod, "check_action_rate_limit", return_value=True),
        ):
            decision = await bot_mod._handle_interrupt(
                {"type": "terminal", "command": "ls"}, bot=MagicMock(), chat_id=42
            )
        assert decision == {"confirmed": True, "rate_limit_ok": True, "command": "ls"}

    @pytest.mark.asyncio
    async def test_terminal_rejected_returns_false(self) -> None:
        from bot import bot as bot_mod

        with patch.object(bot_mod, "request_confirmation", AsyncMock(return_value=False)):
            decision = await bot_mod._handle_interrupt(
                {"type": "terminal", "command": "ls"}, bot=MagicMock(), chat_id=42
            )
        assert decision == {"confirmed": False}

    @pytest.mark.asyncio
    async def test_file_write_confirmed_passes_path_and_content(self) -> None:
        from bot import bot as bot_mod

        with (
            patch.object(bot_mod, "request_confirmation", AsyncMock(return_value=True)),
            patch.object(bot_mod, "check_action_rate_limit", return_value=True),
        ):
            decision = await bot_mod._handle_interrupt(
                {
                    "type": "file_write",
                    "path": "/tmp/x",
                    "content": "hi",
                    "display": "Schreibe nach: /tmp/x",
                },
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision["confirmed"] is True
        assert decision["path"] == "/tmp/x"
        assert decision["content"] == "hi"

    @pytest.mark.asyncio
    async def test_unknown_type_rejected(self) -> None:
        from bot import bot as bot_mod

        decision = await bot_mod._handle_interrupt({"type": "unknown_action"}, bot=MagicMock(), chat_id=42)
        assert decision == {"confirmed": False}
