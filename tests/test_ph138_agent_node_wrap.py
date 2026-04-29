"""
tests/test_ph138_agent_node_wrap.py – Phase 138 (Issue #58)

Testet den wrap_agent_node Decorator:
- Setzt last_agent_result automatisch aus letzter AIMessage wenn nicht explizit gesetzt
- Überschreibt explizit gesetzte Werte NICHT
- Setzt last_agent_name automatisch
- Funktioniert mit None-Returns und leeren Messages
"""

from langchain_core.messages import AIMessage, HumanMessage
from agent.node_utils import wrap_agent_node


class TestWrapAgentNodeAutoResult:
    async def test_sets_last_agent_result_from_ai_message(self):
        async def dummy_agent(state):
            return {"messages": [AIMessage(content="Hallo Welt")]}

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] == "Hallo Welt"

    async def test_sets_last_agent_name(self):
        async def dummy_agent(state):
            return {"messages": [AIMessage(content="x")]}

        wrapped = wrap_agent_node("my_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_name"] == "my_agent"

    async def test_uses_last_ai_message_if_multiple(self):
        async def dummy_agent(state):
            return {
                "messages": [
                    AIMessage(content="erste"),
                    HumanMessage(content="menschlich"),
                    AIMessage(content="letzte"),
                ]
            }

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] == "letzte"


class TestWrapAgentNodeNoOverwrite:
    async def test_does_not_overwrite_explicit_last_agent_result(self):
        async def dummy_agent(state):
            return {
                "messages": [AIMessage(content="AIMessage-Text")],
                "last_agent_result": "explizit gesetzt",
            }

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] == "explizit gesetzt"

    async def test_does_not_overwrite_explicit_none(self):
        async def dummy_agent(state):
            return {
                "messages": [AIMessage(content="hat content")],
                "last_agent_result": None,
            }

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] is None

    async def test_does_not_overwrite_explicit_agent_name(self):
        async def dummy_agent(state):
            return {
                "messages": [AIMessage(content="x")],
                "last_agent_name": "anderer_name",
            }

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_name"] == "anderer_name"


class TestWrapAgentNodeEdgeCases:
    async def test_no_messages_sets_none(self):
        async def dummy_agent(state):
            return {"messages": []}

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] is None

    async def test_no_ai_message_sets_none(self):
        async def dummy_agent(state):
            return {"messages": [HumanMessage(content="nur human")]}

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] is None

    async def test_no_messages_key_sets_none(self):
        async def dummy_agent(state):
            return {}

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["last_agent_result"] is None
        assert result["last_agent_name"] == "dummy_agent"

    async def test_preserves_all_other_keys(self):
        async def dummy_agent(state):
            return {
                "messages": [AIMessage(content="x")],
                "next_agent": "FINISH",
                "telegram_chat_id": 42,
            }

        wrapped = wrap_agent_node("dummy_agent")(dummy_agent)
        result = await wrapped({})
        assert result["next_agent"] == "FINISH"
        assert result["telegram_chat_id"] == 42
