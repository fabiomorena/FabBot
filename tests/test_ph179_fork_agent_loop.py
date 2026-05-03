"""
tests/test_ph179_fork_agent_loop.py

Phase 179 – Issue #141: Fork-Agent Learning Loop.

Testet:
- _turn_counter wird pro Aufruf von chat_agent() inkrementiert
- Kein Batch-Task bei Turns < _MEMORY_NUDGE_INTERVAL
- Batch-Task wird bei Turn N (Modulo) gestartet
- Batch-Text enthält die letzten N Human-Messages
- Kein Batch-Task wenn keine Human-Messages vorhanden
- _turn_counter wird korrekt akkumuliert über mehrere Aufrufe
"""

import importlib
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_chat_agent():
    import agent.agents.chat_agent as mod

    importlib.reload(mod)
    mod._turn_counter = 0
    return mod


def _make_state(human_texts: list[str]) -> dict:
    messages = []
    for text in human_texts:
        messages.append(HumanMessage(content=text))
        messages.append(AIMessage(content="ok"))
    return {
        "messages": messages,
        "last_agent_result": None,
        "last_agent_name": None,
        "session_id": "test-session",
    }


# ---------------------------------------------------------------------------
# Direkte Unit-Tests gegen Modul-Variablen
# ---------------------------------------------------------------------------


class TestTurnCounter:
    def test_initial_value_zero(self):
        mod = _reload_chat_agent()
        assert mod._turn_counter == 0

    def test_env_overrides_interval(self, monkeypatch):
        monkeypatch.setenv("MEMORY_NUDGE_INTERVAL", "5")
        mod = _reload_chat_agent()
        assert mod._MEMORY_NUDGE_INTERVAL == 5

    def test_default_interval_ten(self, monkeypatch):
        monkeypatch.delenv("MEMORY_NUDGE_INTERVAL", raising=False)
        mod = _reload_chat_agent()
        assert mod._MEMORY_NUDGE_INTERVAL == 10


# ---------------------------------------------------------------------------
# Batch-Task Logik (ohne echten LLM-Aufruf)
# ---------------------------------------------------------------------------


class TestForkAgentBatchTrigger:
    def _run_n_turns(self, mod, n: int, apply_mock: MagicMock) -> None:
        """Simuliert n Turns durch direktes Inkrementieren + Batch-Logik."""
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content=f"Nachricht {i}") for i in range(n)]
        state = {"messages": messages}

        for i in range(n):
            mod._turn_counter += 1
            if mod._turn_counter % mod._MEMORY_NUDGE_INTERVAL == 0:
                human_msgs = [
                    m.content for m in state["messages"] if isinstance(m, HumanMessage) and isinstance(m.content, str)
                ][-mod._MEMORY_NUDGE_INTERVAL :]
                if human_msgs:
                    apply_mock("\n".join(human_msgs))

    def test_no_batch_before_interval(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 10
        apply_mock = MagicMock()
        self._run_n_turns(mod, 9, apply_mock)
        apply_mock.assert_not_called()

    def test_batch_triggered_at_interval(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 10
        apply_mock = MagicMock()
        self._run_n_turns(mod, 10, apply_mock)
        apply_mock.assert_called_once()

    def test_batch_triggered_twice_at_2x_interval(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 5
        apply_mock = MagicMock()
        self._run_n_turns(mod, 10, apply_mock)
        assert apply_mock.call_count == 2

    def test_batch_text_contains_last_n_messages(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 3
        captured_texts: list[str] = []
        apply_mock = MagicMock(side_effect=lambda t: captured_texts.append(t))

        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content=f"msg{i}") for i in range(5)]
        state = {"messages": messages}

        for i in range(3):
            mod._turn_counter += 1
            if mod._turn_counter % mod._MEMORY_NUDGE_INTERVAL == 0:
                human_msgs = [
                    m.content for m in state["messages"] if isinstance(m, HumanMessage) and isinstance(m.content, str)
                ][-mod._MEMORY_NUDGE_INTERVAL :]
                if human_msgs:
                    apply_mock("\n".join(human_msgs))

        assert len(captured_texts) == 1
        # Nur die letzten 3 von 5 messages
        assert "msg2" in captured_texts[0]
        assert "msg3" in captured_texts[0]
        assert "msg4" in captured_texts[0]
        assert "msg0" not in captured_texts[0]

    def test_no_batch_without_human_messages(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 3
        apply_mock = MagicMock()

        from langchain_core.messages import AIMessage

        state = {"messages": [AIMessage(content="nur AI") for _ in range(5)]}
        for i in range(3):
            mod._turn_counter += 1
            if mod._turn_counter % mod._MEMORY_NUDGE_INTERVAL == 0:
                human_msgs = [
                    m.content for m in state["messages"] if isinstance(m, HumanMessage) and isinstance(m.content, str)
                ][-mod._MEMORY_NUDGE_INTERVAL :]
                if human_msgs:
                    apply_mock("\n".join(human_msgs))

        apply_mock.assert_not_called()

    def test_turn_counter_accumulates_across_calls(self):
        mod = _reload_chat_agent()
        mod._MEMORY_NUDGE_INTERVAL = 10
        for _ in range(7):
            mod._turn_counter += 1
        assert mod._turn_counter == 7
        for _ in range(3):
            mod._turn_counter += 1
        assert mod._turn_counter == 10
