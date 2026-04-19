"""
Tests für Phase 95 – Issue #2: Prompt-Cache in chat_agent.

Testet:
1. Cache-Hit nach erstem Build (kein zweiter Disk-Read)
2. Cache-Miss nach TTL-Ablauf
3. Cache-Miss nach invalidate_chat_cache()
4. invalidate_chat_cache() wird von profile.write_profile() aufgerufen
5. invalidate_chat_cache() wird von profile.add_note_to_profile() aufgerufen
6. invalidate_chat_cache() wird von claude_md.append_to_claude_md() aufgerufen
7. invalidate_chat_cache() wird von session_summary.summarize_session() aufgerufen
8. _CachedPrompt.is_valid() gibt False zurück nach TTL
9. Fehler in invalidate_chat_cache() bricht Schreibvorgang nicht ab
10. Cache wird nicht genutzt wenn None
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_state(text="Hallo"):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)]}


# ---------------------------------------------------------------------------
# 1. Cache-Hit – kein zweiter Disk-Read
# ---------------------------------------------------------------------------

def test_cache_hit_no_disk_read():
    """Nach erstem Build wird der Cache genutzt – load_claude_md etc. nur einmal."""
    import agent.agents.chat_agent as ca
    ca._prompt_cache = None  # Reset

    call_count = {"n": 0}

    def fake_load_claude_md():
        call_count["n"] += 1
        return "Bot-Instruktion"

    with patch("agent.claude_md.load_claude_md", fake_load_claude_md), \
         patch("bot.session_summary.load_session_summaries", return_value=""), \
         patch("agent.profile.get_profile_context_full", return_value=""):

        result1 = ca._build_chat_prompt()
        result2 = ca._build_chat_prompt()
        result3 = ca._build_chat_prompt()

    assert call_count["n"] == 1, f"load_claude_md sollte nur einmal aufgerufen werden, war {call_count['n']}x"
    assert result1 == result2 == result3


# ---------------------------------------------------------------------------
# 2. Cache-Miss nach TTL-Ablauf
# ---------------------------------------------------------------------------

def test_cache_miss_after_ttl():
    """Nach TTL-Ablauf wird der Cache neu gebaut."""
    import agent.agents.chat_agent as ca
    ca._prompt_cache = None

    call_count = {"n": 0}

    def fake_load():
        call_count["n"] += 1
        return f"Inhalt-{call_count['n']}"

    with patch("agent.claude_md.load_claude_md", fake_load), \
         patch("bot.session_summary.load_session_summaries", return_value=""), \
         patch("agent.profile.get_profile_context_full", return_value=""), \
         patch.object(ca, "_PROMPT_CACHE_TTL", 0.05):  # 50ms TTL für Test

        r1 = ca._build_chat_prompt()
        time.sleep(0.1)  # TTL ablaufen lassen
        r2 = ca._build_chat_prompt()

    assert call_count["n"] == 2, f"Erwartet 2 Aufrufe (1 initial + 1 nach TTL), war {call_count['n']}"
    assert r1 != r2  # Inhalt hat sich geändert


# ---------------------------------------------------------------------------
# 3. Cache-Miss nach invalidate_chat_cache()
# ---------------------------------------------------------------------------

def test_cache_miss_after_invalidate():
    """Nach invalidate_chat_cache() wird beim nächsten Aufruf neu gebaut."""
    import agent.agents.chat_agent as ca
    ca._prompt_cache = None

    call_count = {"n": 0}

    def fake_load():
        call_count["n"] += 1
        return "Inhalt"

    with patch("agent.claude_md.load_claude_md", fake_load), \
         patch("bot.session_summary.load_session_summaries", return_value=""), \
         patch("agent.profile.get_profile_context_full", return_value=""):

        ca._build_chat_prompt()
        assert call_count["n"] == 1

        ca.invalidate_chat_cache()
        assert ca._prompt_cache is None

        ca._build_chat_prompt()
        assert call_count["n"] == 2, "Nach Invalidierung sollte neu gebaut werden"


# ---------------------------------------------------------------------------
# 4. invalidate_chat_cache() wird von write_profile() aufgerufen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_profile_invalidates_cache():
    """write_profile() ruft invalidate_chat_cache() auf nach erfolgreichem Schreiben."""
    import agent.agents.chat_agent as ca
    # Cache vorbelegen
    from agent.agents.chat_agent import _CachedPrompt
    ca._prompt_cache = _CachedPrompt(value="alter Prompt")

    mock_profile = {"identity": {"name": "Fabio"}}

    with patch("agent.profile._PROFILE_PATH") as mock_path, \
         patch("agent.profile.reload_profile"), \
         patch("agent.profile._write_profile_bytes"), \
         patch("agent.crypto.encrypt", return_value=b"encrypted"):

        mock_path.exists.return_value = True

        import yaml
        serialized = yaml.dump(mock_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)

        with patch("yaml.safe_load", return_value=mock_profile), \
             patch("yaml.dump", return_value=serialized):

            from agent.profile import write_profile
            await write_profile(mock_profile)

    assert ca._prompt_cache is None, "Cache sollte nach write_profile() invalidiert sein"


# ---------------------------------------------------------------------------
# 5. invalidate_chat_cache() wird von add_note_to_profile() aufgerufen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_note_invalidates_cache():
    """add_note_to_profile() ruft invalidate_chat_cache() auf."""
    import agent.agents.chat_agent as ca
    from agent.agents.chat_agent import _CachedPrompt
    ca._prompt_cache = _CachedPrompt(value="alter Prompt")

    with patch("agent.profile._PROFILE_PATH") as mock_path, \
         patch("agent.profile.reload_profile"), \
         patch("agent.profile._write_profile_bytes"), \
         patch("agent.crypto.is_encrypted", return_value=False), \
         patch("agent.crypto.encrypt", return_value=b"encrypted"):

        mock_path.exists.return_value = True
        mock_path.read_bytes.return_value = b"notes: []\n"

        import yaml
        with patch("yaml.safe_load", return_value={"notes": []}), \
             patch("yaml.dump", return_value="notes:\n- note\n"):

            from agent.profile import add_note_to_profile
            await add_note_to_profile("Test-Notiz")

    assert ca._prompt_cache is None, "Cache sollte nach add_note_to_profile() invalidiert sein"


# ---------------------------------------------------------------------------
# 6. invalidate_chat_cache() wird von append_to_claude_md() aufgerufen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_claude_md_invalidates_cache():
    """append_to_claude_md() ruft invalidate_chat_cache() auf."""
    import agent.agents.chat_agent as ca
    from agent.agents.chat_agent import _CachedPrompt
    ca._prompt_cache = _CachedPrompt(value="alter Prompt")

    with patch("agent.claude_md._CLAUDE_MD_PATH") as mock_path:
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "# Bot\n"
        mock_path.write_text = MagicMock()

        from agent.claude_md import append_to_claude_md
        result = await append_to_claude_md("Fabio bevorzugt kurze Antworten")

    assert result is True
    assert ca._prompt_cache is None, "Cache sollte nach append_to_claude_md() invalidiert sein"


# ---------------------------------------------------------------------------
# 7. invalidate_chat_cache() wird von summarize_session() aufgerufen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarize_session_invalidates_cache():
    """summarize_session() ruft invalidate_chat_cache() nach erfolgreichem Schreiben auf."""
    import agent.agents.chat_agent as ca
    from agent.agents.chat_agent import _CachedPrompt
    ca._prompt_cache = _CachedPrompt(value="alter Prompt")

    from langchain_core.messages import HumanMessage, AIMessage
    fake_messages = [HumanMessage(content=f"Msg {i}") for i in range(12)] + \
                    [AIMessage(content=f"Ant {i}") for i in range(12)]

    with patch("bot.session_summary._get_messages_from_state", return_value=fake_messages), \
         patch("bot.session_summary._generate_summary", return_value="## Zusammenfassung\nTest"), \
         patch("bot.session_summary._write_summary_file", return_value=True), \
         patch("bot.session_summary._session_path") as mock_path:

        mock_path.return_value.exists.return_value = False

        from bot.session_summary import summarize_session
        from datetime import date
        result = await summarize_session(chat_id=123, target_date=date(2026, 4, 11))

    assert result is True
    assert ca._prompt_cache is None, "Cache sollte nach summarize_session() invalidiert sein"


# ---------------------------------------------------------------------------
# 8. _CachedPrompt.is_valid() gibt False nach TTL
# ---------------------------------------------------------------------------

def test_cached_prompt_is_valid_after_ttl():
    """_CachedPrompt.is_valid() gibt False zurück wenn TTL abgelaufen."""
    import agent.agents.chat_agent as ca

    prompt = ca._CachedPrompt(value="Test")
    assert prompt.is_valid() is True  # frisch erstellt

    # Timestamp in die Vergangenheit setzen
    prompt.timestamp = time.monotonic() - ca._PROMPT_CACHE_TTL - 1
    assert prompt.is_valid() is False


# ---------------------------------------------------------------------------
# 9. Fehler in invalidate_chat_cache() bricht Schreibvorgang nicht ab
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_error_doesnt_break_write():
    """Wenn invalidate_chat_cache() fehlschlägt, soll write_profile() trotzdem True zurückgeben."""
    mock_profile = {"identity": {"name": "Fabio"}}

    with patch("agent.profile._PROFILE_PATH") as mock_path, \
         patch("agent.profile.reload_profile"), \
         patch("agent.profile._write_profile_bytes"), \
         patch("agent.crypto.encrypt", return_value=b"encrypted"), \
         patch("agent.agents.chat_agent.invalidate_chat_cache", side_effect=RuntimeError("Test-Fehler")):

        mock_path.exists.return_value = True

        import yaml
        serialized = yaml.dump(mock_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)

        with patch("yaml.safe_load", return_value=mock_profile), \
             patch("yaml.dump", return_value=serialized):

            from agent.profile import write_profile
            result = await write_profile(mock_profile)

    # Schreiben soll trotzdem funktionieren
    assert result is True


# ---------------------------------------------------------------------------
# 10. Cache None → immer neu bauen
# ---------------------------------------------------------------------------

def test_none_cache_always_rebuilds():
    """Wenn _prompt_cache None ist, wird immer neu gebaut."""
    import agent.agents.chat_agent as ca
    ca._prompt_cache = None

    call_count = {"n": 0}

    def fake_load():
        call_count["n"] += 1
        return ""

    with patch("agent.claude_md.load_claude_md", fake_load), \
         patch("bot.session_summary.load_session_summaries", return_value=""), \
         patch("agent.profile.get_profile_context_full", return_value=""):

        ca._prompt_cache = None
        ca._build_chat_prompt()
        ca._prompt_cache = None
        ca._build_chat_prompt()

    assert call_count["n"] == 2
