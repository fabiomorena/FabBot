"""
Phase 224 (Issue #274, ex #242): HITL via LangGraph interrupt() für computer_agent.

Verifiziert dass computer_agent (click/type/open_app) statt eines
__CONFIRM_COMPUTER__-Magic-Strings einen Pregel-Interrupt auslöst,
der mit Command(resume=...) fortgesetzt wird. Screenshot bleibt unverändert.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command


def _build_single_node_graph(node_fn, state_type):
    g = StateGraph(state_type)
    g.add_node("agent", node_fn)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=MemorySaver())


async def _run_until_interrupt(text: str, thread_id: str):
    from agent.agents.computer import computer_agent
    from agent.state import AgentState

    mock_llm = AsyncMock()
    with patch("agent.agents.computer.get_llm", return_value=mock_llm):
        app = _build_single_node_graph(computer_agent, AgentState)
        config = {"configurable": {"thread_id": thread_id}}
        result = await app.ainvoke(
            {"messages": [HumanMessage(content=text)], "telegram_chat_id": 42},
            config,
        )
    return result


class TestComputerAgentInterrupt:
    @pytest.mark.asyncio
    async def test_click_emits_interrupt(self) -> None:
        result = await _run_until_interrupt("klick auf 100, 200", "comp-click")
        interrupts = result.get("__interrupt__")
        assert interrupts is not None and len(interrupts) == 1
        value = interrupts[0].value
        assert value["type"] == "computer"
        assert value["action"] == "click"
        assert value["x"] == 100
        assert value["y"] == 200

    @pytest.mark.asyncio
    async def test_type_emits_interrupt(self) -> None:
        result = await _run_until_interrupt("tippe Hallo Welt", "comp-type")
        value = result.get("__interrupt__")[0].value
        assert value["type"] == "computer"
        assert value["action"] == "type"
        assert value["text"] == "hallo welt"

    @pytest.mark.asyncio
    async def test_open_app_emits_interrupt(self) -> None:
        result = await _run_until_interrupt("öffne Safari", "comp-open")
        value = result.get("__interrupt__")[0].value
        assert value["type"] == "computer"
        assert value["action"] == "open_app"
        assert "Safari" in value["text"]

    @pytest.mark.asyncio
    async def test_resume_confirmed_executes(self) -> None:
        from agent.agents.computer import computer_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        with (
            patch("agent.agents.computer.get_llm", return_value=mock_llm),
            patch("agent.agents.computer.computer_agent_execute", return_value="Geklickt auf (100, 200).") as mock_exec,
        ):
            app = _build_single_node_graph(computer_agent, AgentState)
            config = {"configurable": {"thread_id": "comp-confirmed"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="klick auf 100, 200")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(
                    resume={
                        "confirmed": True,
                        "rate_limit_ok": True,
                        "action": "click",
                        "x": 100,
                        "y": 200,
                        "text": "",
                    }
                ),
                config,
            )

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "click" and args[1] == 100 and args[2] == 200
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "Geklickt" in ai_msgs[-1].content

    @pytest.mark.asyncio
    async def test_resume_rejected_returns_abbruch(self) -> None:
        from agent.agents.computer import computer_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        with (
            patch("agent.agents.computer.get_llm", return_value=mock_llm),
            patch("agent.agents.computer.computer_agent_execute") as mock_exec,
        ):
            app = _build_single_node_graph(computer_agent, AgentState)
            config = {"configurable": {"thread_id": "comp-rejected"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="klick auf 5, 5")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(Command(resume={"confirmed": False}), config)

        mock_exec.assert_not_called()
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "abgebrochen" in ai_msgs[-1].content.lower()

    @pytest.mark.asyncio
    async def test_resume_rate_limited_blocks(self) -> None:
        from agent.agents.computer import computer_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        with (
            patch("agent.agents.computer.get_llm", return_value=mock_llm),
            patch("agent.agents.computer.computer_agent_execute") as mock_exec,
        ):
            app = _build_single_node_graph(computer_agent, AgentState)
            config = {"configurable": {"thread_id": "comp-rl"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="klick auf 5, 5")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(
                    resume={"confirmed": True, "rate_limit_ok": False, "action": "click", "x": 5, "y": 5, "text": ""}
                ),
                config,
            )

        mock_exec.assert_not_called()
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "rate limit" in ai_msgs[-1].content.lower()


class TestBotHandleInterruptComputer:
    @pytest.mark.asyncio
    async def test_computer_confirmed_passes_fields(self) -> None:
        import bot.bot as bot_mod

        with (
            patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
            patch("bot.bot.check_action_rate_limit", return_value=True),
        ):
            decision = await bot_mod._handle_interrupt(
                {
                    "type": "computer",
                    "action": "click",
                    "x": 100,
                    "y": 200,
                    "text": "",
                    "display": "click @ (100, 200)",
                },
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision["confirmed"] is True
        assert decision["rate_limit_ok"] is True
        assert decision["action"] == "click"
        assert decision["x"] == 100
        assert decision["y"] == 200

    @pytest.mark.asyncio
    async def test_computer_rejected_returns_false(self) -> None:
        import bot.bot as bot_mod

        with patch("bot.bot.request_confirmation", AsyncMock(return_value=False)):
            decision = await bot_mod._handle_interrupt(
                {"type": "computer", "action": "click", "x": 1, "y": 1, "text": ""},
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision == {"confirmed": False}
