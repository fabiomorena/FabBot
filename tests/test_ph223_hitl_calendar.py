"""
Phase 223 (Issue #274, ex #243): HITL via LangGraph interrupt() für calendar_agent.

Verifiziert dass calendar_agent bei create_event statt eines
__CONFIRM_CREATE_EVENT__-Magic-Strings einen Pregel-Interrupt auslöst,
der mit Command(resume=...) fortgesetzt wird.
"""

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
    g = StateGraph(state_type)
    g.add_node("agent", node_fn)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=MemorySaver())


_CREATE_JSON = (
    '{"action": "create_event", "title": "Zahnarzt", '
    '"start_time": "2026-07-01T10:00:00", "end_time": "2026-07-01T11:00:00"}'
)


class TestCalendarAgentInterrupt:
    @pytest.mark.asyncio
    async def test_create_event_emits_interrupt(self) -> None:
        from agent.agents.calendar import calendar_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_CREATE_JSON))

        with patch("agent.agents.calendar.get_llm", return_value=mock_llm):
            app = _build_single_node_graph(calendar_agent, AgentState)
            config = {"configurable": {"thread_id": "cal-1"}}
            result = await app.ainvoke(
                {"messages": [HumanMessage(content="trag zahnarzt ein")], "telegram_chat_id": 42},
                config,
            )

        interrupts = result.get("__interrupt__")
        assert interrupts is not None
        assert len(interrupts) == 1
        value = interrupts[0].value
        assert value["type"] == "create_event"
        assert value["title"] == "Zahnarzt"
        assert value["start_time"] == "2026-07-01T10:00:00"
        assert value["end_time"] == "2026-07-01T11:00:00"

    @pytest.mark.asyncio
    async def test_resume_confirmed_creates_event(self) -> None:
        from agent.agents.calendar import calendar_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_CREATE_JSON))

        with (
            patch("agent.agents.calendar.get_llm", return_value=mock_llm),
            patch(
                "agent.agents.calendar.calendar_event_create", return_value="Termin erstellt: Zahnarzt"
            ) as mock_create,
        ):
            app = _build_single_node_graph(calendar_agent, AgentState)
            config = {"configurable": {"thread_id": "cal-confirmed"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="trag ein")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(
                    resume={
                        "confirmed": True,
                        "title": "Zahnarzt",
                        "start_time": "2026-07-01T10:00:00",
                        "end_time": "2026-07-01T11:00:00",
                    }
                ),
                config,
            )

        mock_create.assert_called_once()
        args = mock_create.call_args[0]
        assert args[0] == "Zahnarzt"
        assert args[1] == "2026-07-01T10:00:00"
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs
        assert "erstellt" in ai_msgs[-1].content.lower()

    @pytest.mark.asyncio
    async def test_resume_rejected_returns_abbruch(self) -> None:
        from agent.agents.calendar import calendar_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_CREATE_JSON))

        with (
            patch("agent.agents.calendar.get_llm", return_value=mock_llm),
            patch("agent.agents.calendar.calendar_event_create") as mock_create,
        ):
            app = _build_single_node_graph(calendar_agent, AgentState)
            config = {"configurable": {"thread_id": "cal-rejected"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="trag ein")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(Command(resume={"confirmed": False}), config)

        mock_create.assert_not_called()
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs
        assert "abgebrochen" in ai_msgs[-1].content.lower()


class TestBotHandleInterruptCreateEvent:
    """_handle_interrupt() in bot.py – Brücke für type=create_event."""

    @pytest.mark.asyncio
    async def test_create_event_confirmed_passes_fields(self) -> None:
        import bot.bot as bot_mod

        with patch("bot.bot.request_confirmation", AsyncMock(return_value=True)):
            decision = await bot_mod._handle_interrupt(
                {
                    "type": "create_event",
                    "title": "Zahnarzt",
                    "start_time": "2026-07-01T10:00:00",
                    "end_time": "2026-07-01T11:00:00",
                    "display": "Neuer Termin",
                },
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision["confirmed"] is True
        assert decision["title"] == "Zahnarzt"
        assert decision["start_time"] == "2026-07-01T10:00:00"
        assert decision["end_time"] == "2026-07-01T11:00:00"

    @pytest.mark.asyncio
    async def test_create_event_rejected_returns_false(self) -> None:
        import bot.bot as bot_mod

        with patch("bot.bot.request_confirmation", AsyncMock(return_value=False)):
            decision = await bot_mod._handle_interrupt(
                {"type": "create_event", "title": "X", "start_time": "2026-07-01T10:00:00", "end_time": ""},
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision == {"confirmed": False}
