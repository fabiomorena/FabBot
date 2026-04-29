"""
Tests für Phase 164 – Anthropic Prompt Caching.

Testet:
1. chat_agent: SystemMessage enthält cache_control auf statischem Block
2. chat_agent: Dynamischer Block (Uhrzeit, Retrieval) hat kein cache_control
3. chat_agent: Kein dynamischer Block wenn Suffix leer
4. supervisor: SystemMessage enthält cache_control auf SUPERVISOR_PROMPT
5. supervisor: Sanitized User-Message hat kein cache_control
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, SystemMessage


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _make_chat_state(text="Was ist die aktuelle Uhrzeit?"):
    return {
        "messages": [HumanMessage(content=text)],
        "last_agent_result": None,
        "last_agent_name": None,
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
        "telegram_chat_id": None,
        "next_agent": None,
    }


def _extract_system_message(messages: list) -> SystemMessage | None:
    for m in messages:
        if isinstance(m, SystemMessage):
            return m
    return None


# ---------------------------------------------------------------------------
# 1. chat_agent: cache_control auf statischem Block vorhanden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_agent_static_block_has_cache_control():
    """chat_agent schickt cache_control auf dem ersten (statischen) Content-Block."""
    import agent.agents.chat_agent as ca

    captured = {}

    async def fake_ainvoke(msgs):
        captured["messages"] = msgs
        return MagicMock(content="OK")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_ainvoke

    with (
        patch("agent.agents.chat_agent.get_llm", return_value=fake_llm),
        patch("agent.agents.chat_agent._build_chat_prompt", return_value="STATISCH"),
        patch("agent.agents.chat_agent._build_dynamic_prompt_suffix", return_value="\n[Uhrzeit: 09:00]"),
        patch("agent.agents.chat_agent._get_retrieval_context", new=AsyncMock(return_value="")),
        patch("agent.agents.chat_agent._is_short_confirmation", return_value=False),
        patch("agent.agents.chat_agent._get_context_window_size", return_value=20),
    ):
        state = _make_chat_state()
        await ca.chat_agent(state)

    sys_msg = _extract_system_message(captured["messages"])
    assert sys_msg is not None
    assert isinstance(sys_msg.content, list), "Content muss eine Liste von Blöcken sein"

    first_block = sys_msg.content[0]
    assert first_block.get("cache_control") == {"type": "ephemeral"}, (
        f"Erster Block soll cache_control haben, hat: {first_block}"
    )
    assert first_block["text"] == "STATISCH"


# ---------------------------------------------------------------------------
# 2. Dynamischer Block hat kein cache_control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_agent_dynamic_block_no_cache_control():
    """Dynamischer Suffix-Block (Uhrzeit, Retrieval) hat kein cache_control."""
    import agent.agents.chat_agent as ca

    captured = {}

    async def fake_ainvoke(msgs):
        captured["messages"] = msgs
        return MagicMock(content="OK")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_ainvoke

    with (
        patch("agent.agents.chat_agent.get_llm", return_value=fake_llm),
        patch("agent.agents.chat_agent._build_chat_prompt", return_value="STATISCH"),
        patch("agent.agents.chat_agent._build_dynamic_prompt_suffix", return_value="\n[Uhrzeit: 09:00]"),
        patch("agent.agents.chat_agent._get_retrieval_context", new=AsyncMock(return_value="[RAG-Treffer]")),
        patch("agent.agents.chat_agent._is_short_confirmation", return_value=False),
        patch("agent.agents.chat_agent._get_context_window_size", return_value=20),
    ):
        state = _make_chat_state()
        await ca.chat_agent(state)

    sys_msg = _extract_system_message(captured["messages"])
    assert isinstance(sys_msg.content, list)
    assert len(sys_msg.content) == 2, "Erwartet genau zwei Blöcke (statisch + dynamisch)"

    dynamic_block = sys_msg.content[1]
    assert "cache_control" not in dynamic_block, f"Dynamischer Block darf kein cache_control haben: {dynamic_block}"
    assert "[Uhrzeit: 09:00]" in dynamic_block["text"]
    assert "[RAG-Treffer]" in dynamic_block["text"]


# ---------------------------------------------------------------------------
# 3. Kein dynamischer Block wenn Suffix leer (z.B. kurze Bestätigung)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_agent_no_dynamic_block_when_suffix_empty():
    """Wenn Suffix leer ist, enthält content nur einen Block."""
    import agent.agents.chat_agent as ca

    captured = {}

    async def fake_ainvoke(msgs):
        captured["messages"] = msgs
        return MagicMock(content="Gern!")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_ainvoke

    with (
        patch("agent.agents.chat_agent.get_llm", return_value=fake_llm),
        patch("agent.agents.chat_agent._build_chat_prompt", return_value="STATISCH"),
        patch("agent.agents.chat_agent._build_dynamic_prompt_suffix", return_value="   "),
        patch("agent.agents.chat_agent._get_retrieval_context", new=AsyncMock(return_value="")),
        patch("agent.agents.chat_agent._is_short_confirmation", return_value=True),
        patch("agent.agents.chat_agent._get_context_window_size", return_value=20),
    ):
        state = _make_chat_state("Danke")
        await ca.chat_agent(state)

    sys_msg = _extract_system_message(captured["messages"])
    assert isinstance(sys_msg.content, list)
    assert len(sys_msg.content) == 1, "Nur ein Block erwartet wenn Suffix leer"
    assert sys_msg.content[0].get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# 4. supervisor: SUPERVISOR_PROMPT mit cache_control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_prompt_has_cache_control():
    """supervisor_node schickt SUPERVISOR_PROMPT mit cache_control."""
    from agent import supervisor as sup

    captured = {}

    async def fake_ainvoke(msgs):
        captured["messages"] = msgs
        return MagicMock(content="chat_agent")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_ainvoke

    state = {
        "messages": [HumanMessage(content="Was ist los?")],
        "image_data": None,
        "last_agent_result": None,
        "last_agent_name": None,
        "telegram_chat_id": None,
        "next_agent": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.supervisor.get_fast_llm", return_value=fake_llm):
        await sup.supervisor_node(state)

    sys_msg = _extract_system_message(captured["messages"])
    assert sys_msg is not None
    assert isinstance(sys_msg.content, list)

    first_block = sys_msg.content[0]
    assert first_block.get("cache_control") == {"type": "ephemeral"}, (
        f"SUPERVISOR_PROMPT muss cache_control haben: {first_block}"
    )
    assert first_block["text"] == sup.SUPERVISOR_PROMPT


# ---------------------------------------------------------------------------
# 5. supervisor: User-Message hat kein cache_control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_user_message_no_cache_control():
    """Die sanitized User-Message hat kein cache_control."""
    from agent import supervisor as sup

    captured = {}

    async def fake_ainvoke(msgs):
        captured["messages"] = msgs
        return MagicMock(content="web_agent")

    fake_llm = MagicMock()
    fake_llm.ainvoke = fake_ainvoke

    state = {
        "messages": [HumanMessage(content="Wetter Berlin heute")],
        "image_data": None,
        "last_agent_result": None,
        "last_agent_name": None,
        "telegram_chat_id": None,
        "next_agent": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.supervisor.get_fast_llm", return_value=fake_llm):
        await sup.supervisor_node(state)

    # Letzte Message ist HumanMessage ohne cache_control
    human_msgs = [m for m in captured["messages"] if isinstance(m, HumanMessage)]
    assert human_msgs, "Erwartet mindestens eine HumanMessage"
    content = human_msgs[-1].content
    if isinstance(content, list):
        for block in content:
            assert "cache_control" not in block
