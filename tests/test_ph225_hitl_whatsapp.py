"""
Phase 225 (Issue #274, ex #244): HITL via LangGraph interrupt() für whatsapp_agent.

Verifiziert dass whatsapp_agent statt eines __CONFIRM_WHATSAPP__-Magic-Strings
einen Pregel-Interrupt auslöst. Besonderheit: der Versand (async I/O via
send_whatsapp_message) passiert im Node NACH dem Resume.
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


_PARSE_JSON = '{"contact": "Steffi", "message": "Komme 10 Minuten später"}'


def _patch_whatsapp_env():
    """Patcht die lazy-importierten bot.whatsapp-Funktionen im Node."""
    return patch.multiple(
        "bot.whatsapp",
        is_session_ready=MagicMock(return_value=True),
        find_contact=MagicMock(return_value={"whatsapp_name": "Steffi W."}),
    )


async def _run_to_interrupt(thread_id: str):
    from agent.agents.whatsapp_agent import whatsapp_agent
    from agent.state import AgentState

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_PARSE_JSON))
    with patch("agent.agents.whatsapp_agent.get_llm", return_value=mock_llm), _patch_whatsapp_env():
        app = _build_single_node_graph(whatsapp_agent, AgentState)
        config = {"configurable": {"thread_id": thread_id}}
        result = await app.ainvoke(
            {"messages": [HumanMessage(content="schick steffi dass ich später komme")], "telegram_chat_id": 42},
            config,
        )
    return result


class TestWhatsappAgentInterrupt:
    @pytest.mark.asyncio
    async def test_emits_interrupt(self) -> None:
        result = await _run_to_interrupt("wa-1")
        interrupts = result.get("__interrupt__")
        assert interrupts is not None and len(interrupts) == 1
        value = interrupts[0].value
        assert value["type"] == "whatsapp"
        assert value["whatsapp_name"] == "Steffi W."
        assert "10 Minuten" in value["message"]

    @pytest.mark.asyncio
    async def test_resume_confirmed_sends(self) -> None:
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_PARSE_JSON))
        mock_send = AsyncMock(return_value=(True, "Nachricht an Steffi W. gesendet."))
        with (
            patch("agent.agents.whatsapp_agent.get_llm", return_value=mock_llm),
            _patch_whatsapp_env(),
            patch("bot.whatsapp.send_whatsapp_message", mock_send),
        ):
            app = _build_single_node_graph(whatsapp_agent, AgentState)
            config = {"configurable": {"thread_id": "wa-confirmed"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="schick steffi")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(
                    resume={
                        "confirmed": True,
                        "rate_limit_ok": True,
                        "whatsapp_name": "Steffi W.",
                        "message": "Komme 10 Minuten später",
                    }
                ),
                config,
            )

        mock_send.assert_awaited_once()
        args = mock_send.call_args[0]
        assert args[0] == "Steffi W."
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "gesendet" in ai_msgs[-1].content.lower()

    @pytest.mark.asyncio
    async def test_resume_rejected_does_not_send(self) -> None:
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_PARSE_JSON))
        mock_send = AsyncMock()
        with (
            patch("agent.agents.whatsapp_agent.get_llm", return_value=mock_llm),
            _patch_whatsapp_env(),
            patch("bot.whatsapp.send_whatsapp_message", mock_send),
        ):
            app = _build_single_node_graph(whatsapp_agent, AgentState)
            config = {"configurable": {"thread_id": "wa-rejected"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="schick steffi")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(Command(resume={"confirmed": False}), config)

        mock_send.assert_not_awaited()
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "abgebrochen" in ai_msgs[-1].content.lower()

    @pytest.mark.asyncio
    async def test_resume_rate_limited_does_not_send(self) -> None:
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.state import AgentState

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_llm_response(_PARSE_JSON))
        mock_send = AsyncMock()
        with (
            patch("agent.agents.whatsapp_agent.get_llm", return_value=mock_llm),
            _patch_whatsapp_env(),
            patch("bot.whatsapp.send_whatsapp_message", mock_send),
        ):
            app = _build_single_node_graph(whatsapp_agent, AgentState)
            config = {"configurable": {"thread_id": "wa-rl"}}
            await app.ainvoke(
                {"messages": [HumanMessage(content="schick steffi")], "telegram_chat_id": 42},
                config,
            )
            final = await app.ainvoke(
                Command(
                    resume={
                        "confirmed": True,
                        "rate_limit_ok": False,
                        "whatsapp_name": "Steffi W.",
                        "message": "X",
                    }
                ),
                config,
            )

        mock_send.assert_not_awaited()
        ai_msgs = [m for m in final["messages"] if isinstance(m, AIMessage)]
        assert ai_msgs and "rate limit" in ai_msgs[-1].content.lower()


class TestBotHandleInterruptWhatsapp:
    @pytest.mark.asyncio
    async def test_whatsapp_confirmed_passes_fields(self) -> None:
        import bot.bot as bot_mod

        with (
            patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
            patch("bot.bot.check_action_rate_limit", return_value=True),
        ):
            decision = await bot_mod._handle_interrupt(
                {
                    "type": "whatsapp",
                    "whatsapp_name": "Steffi W.",
                    "message": "Hallo",
                    "display": "WhatsApp an Steffi",
                },
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision["confirmed"] is True
        assert decision["rate_limit_ok"] is True
        assert decision["whatsapp_name"] == "Steffi W."
        assert decision["message"] == "Hallo"

    @pytest.mark.asyncio
    async def test_whatsapp_rejected_returns_false(self) -> None:
        import bot.bot as bot_mod

        with patch("bot.bot.request_confirmation", AsyncMock(return_value=False)):
            decision = await bot_mod._handle_interrupt(
                {"type": "whatsapp", "whatsapp_name": "X", "message": "Y"},
                bot=MagicMock(),
                chat_id=42,
            )
        assert decision == {"confirmed": False}
