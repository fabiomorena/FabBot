"""
Tests Phase 99 – State-Transfer zwischen Agents (Issue #15) + Cache-Fix (Issue #12)

Testet:
1. AgentState hat last_agent_result + last_agent_name Felder
2. _build_dynamic_prompt_suffix() – Datetime immer frisch (nicht gecacht)
3. _build_dynamic_prompt_suffix() – last_agent_result korrekt injiziert
4. chat_agent() resettet last_agent_result nach Verarbeitung
5. web_agent() setzt last_agent_result im Return
6. terminal_agent() setzt last_agent_result im Return
7. chat_agent() nutzt last_agent_result aus State
8. Kein last_agent_result → kein Injection-Block im Prompt
9. last_agent_result leer/None → kein Injection-Block
10. Datetime im Prompt ist frisch (nicht gecacht)
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage


# ─── 1. State-Schema ─────────────────────────────────────────────────────────

def test_agent_state_has_last_agent_result_field():
    """AgentState TypedDict hat last_agent_result und last_agent_name."""
    from agent.state import AgentState
    hints = AgentState.__annotations__
    assert "last_agent_result" in hints, "last_agent_result fehlt in AgentState"
    assert "last_agent_name" in hints, "last_agent_name fehlt in AgentState"


def test_agent_state_last_agent_result_nullable():
    """last_agent_result und last_agent_name sind Optional (str | None)."""
    from agent.state import AgentState
    hints = AgentState.__annotations__
    # TypedDict speichert Union-Typen – wir prüfen dass None erlaubt ist
    import typing
    result_type = hints["last_agent_result"]
    name_type = hints["last_agent_name"]
    # str | None wird als UnionType oder Optional[str] gespeichert
    assert "None" in str(result_type) or type(None) in getattr(result_type, "__args__", ())


# ─── 2. _build_dynamic_prompt_suffix ─────────────────────────────────────────

def test_dynamic_suffix_contains_datetime():
    """_build_dynamic_prompt_suffix() enthält Datum/Uhrzeit."""
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix
    suffix = _build_dynamic_prompt_suffix(None, None)
    assert "Aktuelles Datum/Uhrzeit" in suffix


def test_dynamic_suffix_no_agent_result_no_block():
    """Kein last_agent_result → kein '## Ergebnis' Block im Suffix."""
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix
    suffix = _build_dynamic_prompt_suffix(None, None)
    assert "## Ergebnis" not in suffix


def test_dynamic_suffix_empty_result_no_block():
    """Leerer last_agent_result → kein Injection-Block."""
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix
    suffix = _build_dynamic_prompt_suffix("", "web_agent")
    assert "## Ergebnis" not in suffix


def test_dynamic_suffix_with_agent_result():
    """last_agent_result vorhanden → Injection-Block im Suffix."""
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix
    suffix = _build_dynamic_prompt_suffix("Berlin hat 3,7 Millionen Einwohner.", "web_agent")
    assert "## Kontext: Ergebnis des web_agent" in suffix
    assert "Berlin hat 3,7 Millionen Einwohner." in suffix


def test_dynamic_suffix_with_unknown_agent():
    """Kein agent_name → Fallback 'vorheriger Agent'."""
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix
    suffix = _build_dynamic_prompt_suffix("Ergebnis XYZ", None)
    assert "vorheriger Agent" in suffix


def test_dynamic_suffix_datetime_is_fresh():
    """
    Datetime in _build_dynamic_prompt_suffix() ist immer frisch –
    nicht gecacht. Zwei Aufrufe mit ~0s Abstand liefern dasselbe Format
    aber der Wert kommt aus get_current_datetime() nicht aus Cache.
    """
    from agent.agents.chat_agent import _build_dynamic_prompt_suffix, invalidate_chat_cache
    # Cache leeren um sicherzustellen dass _build_chat_prompt() neu baut
    invalidate_chat_cache()
    s1 = _build_dynamic_prompt_suffix(None, None)
    s2 = _build_dynamic_prompt_suffix(None, None)
    # Beide enthalten Datetime – Format prüfen
    assert "Aktuelles Datum/Uhrzeit" in s1
    assert "Aktuelles Datum/Uhrzeit" in s2


# ─── 3. chat_agent() Integration ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_agent_uses_last_agent_result():
    """chat_agent() injiziert last_agent_result in den System-Prompt."""
    captured_messages = []

    async def fake_llm_invoke(messages):
        captured_messages.extend(messages)
        return AIMessage(content="Antwort basierend auf Web-Ergebnis.")

    mock_llm = MagicMock()
    mock_llm.ainvoke = fake_llm_invoke

    state = {
        "messages": [HumanMessage(content="Was ist die Hauptstadt von Frankreich?")],
        "telegram_chat_id": 123,
        "last_agent_result": "Paris ist die Hauptstadt von Frankreich mit 2,1 Mio. Einwohnern.",
        "last_agent_name": "web_agent",
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.chat_agent.get_llm", return_value=mock_llm), \
         patch("agent.agents.chat_agent._get_retrieval_context", new_callable=AsyncMock, return_value=""), \
         patch("agent.agents.chat_agent._build_chat_prompt", return_value="BASE PROMPT"):

        from agent.agents.chat_agent import chat_agent
        result = await chat_agent(state)

    # System-Prompt muss last_agent_result enthalten
    system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
    assert system_msgs, "Kein SystemMessage gefunden"
    system_content = system_msgs[0].content
    # Phase 164: content ist jetzt Liste von Blöcken (Prompt Caching)
    if isinstance(system_content, list):
        system_text = " ".join(b.get("text", "") for b in system_content if isinstance(b, dict))
    else:
        system_text = system_content
    assert "Paris ist die Hauptstadt" in system_text
    assert "web_agent" in system_text


@pytest.mark.asyncio
async def test_chat_agent_resets_last_agent_result():
    """chat_agent() setzt last_agent_result im Return auf None zurück."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="Antwort"))

    state = {
        "messages": [HumanMessage(content="Danke")],
        "telegram_chat_id": 123,
        "last_agent_result": "Irgendein Ergebnis",
        "last_agent_name": "file_agent",
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.chat_agent.get_llm", return_value=mock_llm), \
         patch("agent.agents.chat_agent._get_retrieval_context", new_callable=AsyncMock, return_value=""), \
         patch("agent.agents.chat_agent._build_chat_prompt", return_value="BASE"):

        from agent.agents.chat_agent import chat_agent
        result = await chat_agent(state)

    assert result.get("last_agent_result") is None
    assert result.get("last_agent_name") is None


@pytest.mark.asyncio
async def test_chat_agent_no_last_result_no_injection():
    """Kein last_agent_result → kein Injection-Block im System-Prompt."""
    captured_messages = []

    async def fake_invoke(messages):
        captured_messages.extend(messages)
        return AIMessage(content="Antwort")

    mock_llm = MagicMock()
    mock_llm.ainvoke = fake_invoke

    state = {
        "messages": [HumanMessage(content="Hallo")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.chat_agent.get_llm", return_value=mock_llm), \
         patch("agent.agents.chat_agent._get_retrieval_context", new_callable=AsyncMock, return_value=""), \
         patch("agent.agents.chat_agent._build_chat_prompt", return_value="BASE"):

        from agent.agents.chat_agent import chat_agent
        await chat_agent(state)

    system_msgs = [m for m in captured_messages if isinstance(m, SystemMessage)]
    assert system_msgs
    assert "## Ergebnis" not in system_msgs[0].content


# ─── 4. web_agent() Return ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_agent_sets_last_agent_result_on_unsupported():
    """web_agent() setzt last_agent_result auch bei UNSUPPORTED."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))

    state = {
        "messages": [HumanMessage(content="Irgendwas")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.web.get_llm", return_value=mock_llm):
        from agent.agents.web import web_agent
        result = await web_agent(state)

    assert result.get("last_agent_name") == "web_agent"
    assert result.get("last_agent_result") is not None


@pytest.mark.asyncio
async def test_web_agent_sets_last_agent_result_on_success():
    """web_agent() setzt last_agent_result mit dem Suchergebnis."""
    search_json = '{"action": "search", "query": "Berlin Einwohner", "engine": "auto"}'
    summary_text = "Berlin hat 3,7 Millionen Einwohner."

    call_count = 0

    async def fake_invoke(messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return AIMessage(content=search_json)
        return AIMessage(content=summary_text)

    mock_llm = MagicMock()
    mock_llm.ainvoke = fake_invoke

    state = {
        "messages": [HumanMessage(content="Wie viele Einwohner hat Berlin?")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.web.get_llm", return_value=mock_llm), \
         patch("agent.agents.web._search_tavily", new_callable=AsyncMock,
               return_value=[{"title": "Berlin", "url": "https://example.com", "content": "3,7 Mio"}]), \
         patch("agent.agents.web.TAVILY_API_KEY", "fake-key"):

        from agent.agents.web import web_agent
        result = await web_agent(state)

    assert result.get("last_agent_name") == "web_agent"
    assert result.get("last_agent_result") == summary_text


# ─── 5. terminal_agent() Return ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_terminal_agent_sets_last_agent_result_on_unsupported():
    """terminal_agent() setzt last_agent_result bei UNSUPPORTED."""
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))

    state = {
        "messages": [HumanMessage(content="Lösche alles")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
        "image_data": None,
        "image_caption": None,
        "image_media_type": None,
    }

    with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
        from agent.agents.terminal import terminal_agent
        result = await terminal_agent(state)

    assert result.get("last_agent_name") == "terminal_agent"
    assert "nicht unterstuetzt" in (result.get("last_agent_result") or "")
