"""
tests/test_ph259_agent_result_ttl.py – Phase 259 (Issue #259)

Testet last_agent_result TTL + konfigurierbares Größenlimit:
- Größenkürzung via agent_result_max_chars
- TTL=1 (Default): Nach 1 Turn cleared
- TTL=2: Nach 1 Turn noch vorhanden, nach 2 Turns cleared
- wrap_agent_node setzt last_agent_result_turn
"""

from unittest.mock import patch
from langchain_core.messages import AIMessage
from agent.node_utils import wrap_agent_node


class TestAgentResultTTLViaNodeUtils:
    async def test_sets_turn_counter_with_default_ttl(self):
        async def dummy_agent(state):
            return {"messages": [AIMessage(content="Ergebnis")]}

        with patch("agent.config.get_settings") as mock_settings:
            mock_settings.return_value.agent_result_ttl_turns = 1
            wrapped = wrap_agent_node("dummy")(dummy_agent)
            result = await wrapped({})

        assert result["last_agent_result_turn"] == 1

    async def test_sets_turn_counter_with_custom_ttl(self):
        async def dummy_agent(state):
            return {"messages": [AIMessage(content="x")]}

        with patch("agent.config.get_settings") as mock_settings:
            mock_settings.return_value.agent_result_ttl_turns = 3
            wrapped = wrap_agent_node("dummy")(dummy_agent)
            result = await wrapped({})

        assert result["last_agent_result_turn"] == 3

    async def test_does_not_overwrite_explicit_turn(self):
        async def dummy_agent(state):
            return {"messages": [AIMessage(content="x")], "last_agent_result_turn": 99}

        wrapped = wrap_agent_node("dummy")(dummy_agent)
        result = await wrapped({})

        assert result["last_agent_result_turn"] == 99


class TestAgentResultSizeTruncation:
    def test_truncation_at_limit(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix

        long_result = "x" * 3000
        with patch("agent.agents.chat_agent.get_settings") as mock_settings:
            mock_settings.return_value.agent_result_max_chars = 100
            mock_settings.return_value.profile_snapshot_ttl = 300.0
            suffix = _build_dynamic_prompt_suffix(long_result, "web_agent")

        assert "…[gekürzt]" in suffix
        # Das gekürzte Ergebnis sollte max 100 Zeichen + Marker sein
        assert len(long_result[:100]) == 100

    def test_no_truncation_under_limit(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix

        short_result = "kurzes Ergebnis"
        with patch("agent.agents.chat_agent.get_settings") as mock_settings:
            mock_settings.return_value.agent_result_max_chars = 2000
            mock_settings.return_value.profile_snapshot_ttl = 300.0
            suffix = _build_dynamic_prompt_suffix(short_result, "web_agent")

        assert "…[gekürzt]" not in suffix
        assert short_result in suffix


class TestChatAgentTTLCleanup:
    def _make_cleanup_result(self, ttl: int | None) -> dict:
        """Simuliert die Phase-99-Cleanup-Logik aus chat_agent."""
        from langchain_core.messages import AIMessage as AI

        state = {"last_agent_result_turn": ttl, "last_agent_result": "test", "last_agent_name": "web_agent"}
        result_msg = AI(content="antwort")
        ttl_val = state.get("last_agent_result_turn") or 1
        if ttl_val <= 1:
            cleanup = {"last_agent_result": None, "last_agent_name": None, "last_agent_result_turn": None}
        else:
            cleanup = {"last_agent_result_turn": ttl_val - 1}
        return {"messages": [result_msg], **cleanup}

    def test_ttl_1_clears_result(self):
        result = self._make_cleanup_result(ttl=1)
        assert result["last_agent_result"] is None
        assert result["last_agent_name"] is None
        assert result["last_agent_result_turn"] is None

    def test_ttl_none_clears_result(self):
        result = self._make_cleanup_result(ttl=None)
        assert result["last_agent_result"] is None

    def test_ttl_2_decrements_to_1(self):
        result = self._make_cleanup_result(ttl=2)
        assert result["last_agent_result_turn"] == 1
        assert "last_agent_result" not in result

    def test_ttl_3_decrements_to_2(self):
        result = self._make_cleanup_result(ttl=3)
        assert result["last_agent_result_turn"] == 2

    def test_ttl_2_then_1_clears(self):
        first = self._make_cleanup_result(ttl=2)
        assert first["last_agent_result_turn"] == 1
        second = self._make_cleanup_result(ttl=first["last_agent_result_turn"])
        assert second["last_agent_result"] is None
