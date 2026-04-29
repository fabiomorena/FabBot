"""
tests/test_ph152_heartbeat_context.py – Phase 152 (Issue #95)

Testet die Heartbeat-Kontextanreicherung:
- _fetch_profile_ctx: Profil-Daten via get_profile_context_short
- _fetch_memory_ctx: ChromaDB-Similarity-Search
- _fetch_session_ctx: Session-History mit Entity-Filter
- _gather_heartbeat_context: paralleles Laden aller 3 Quellen mit Timeout
- generate_proactive_message: Prompt enthält alle 3 Blöcke
- load_all_sessions: Public-Export aus chat_agent
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


TRIGGER_ITEM = {
    "name": "Salvador",
    "entity_type": "place",
    "due_date": "2026-05-31",
    "days_until_due": 7,
    "source_context": "Reise nach Salvador Ende Mai",
}


# ── load_all_sessions Public-Export ──────────────────────────────────────────


class TestLoadAllSessionsExport:
    def test_is_importable(self):
        from agent.agents.chat_agent import load_all_sessions

        assert callable(load_all_sessions)

    def test_delegates_to_private(self):
        from agent.agents.chat_agent import load_all_sessions

        with patch("agent.agents.chat_agent._load_all_sessions", return_value="session-data") as mock:
            result = load_all_sessions()
        mock.assert_called_once_with(None)
        assert result == "session-data"

    def test_passes_max_days(self):
        from agent.agents.chat_agent import load_all_sessions

        with patch("agent.agents.chat_agent._load_all_sessions", return_value="") as mock:
            load_all_sessions(max_days=14)
        mock.assert_called_once_with(14)


# ── _fetch_profile_ctx ────────────────────────────────────────────────────────


class TestFetchProfileCtx:
    async def test_returns_profile_string(self):
        from agent.proactive.heartbeat import _fetch_profile_ctx

        with patch("agent.profile.get_profile_context_short", return_value="User: Fabio (Berlin)"):
            result = await _fetch_profile_ctx()
        assert result == "User: Fabio (Berlin)"

    async def test_returns_empty_on_exception(self):
        from agent.proactive.heartbeat import _fetch_profile_ctx

        with patch("agent.profile.get_profile_context_short", side_effect=Exception("disk error")):
            result = await _fetch_profile_ctx()
        assert result == ""

    async def test_returns_empty_when_profile_empty(self):
        from agent.proactive.heartbeat import _fetch_profile_ctx

        with patch("agent.profile.get_profile_context_short", return_value=""):
            result = await _fetch_profile_ctx()
        assert result == ""


# ── _fetch_memory_ctx ─────────────────────────────────────────────────────────


class TestFetchMemoryCtx:
    async def test_empty_for_short_query(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        result = await _fetch_memory_ctx("ab")
        assert result == ""

    async def test_empty_for_blank_query(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        result = await _fetch_memory_ctx("")
        assert result == ""

    async def test_formats_results_with_label_prefix(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        mock_results = [
            {"label": "Reise", "document": "Fabio plant Reise nach Salvador."},
            {"label": "Musik", "document": "Projekt FabBot läuft stabil."},
        ]
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=mock_results):
            result = await _fetch_memory_ctx("Salvador Reise")
        assert "[Reise]" in result
        assert "[Musik]" in result
        assert "Salvador" in result

    async def test_returns_empty_on_no_results(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=[]):
            result = await _fetch_memory_ctx("unbekannt xyz")
        assert result == ""

    async def test_returns_empty_on_search_exception(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        with patch("agent.retrieval.search", new_callable=AsyncMock, side_effect=Exception("chroma down")):
            result = await _fetch_memory_ctx("Salvador")
        assert result == ""

    async def test_truncates_long_document(self):
        from agent.proactive.heartbeat import _fetch_memory_ctx

        long_doc = "x" * 500
        mock_results = [{"label": "Test", "document": long_doc}]
        with patch("agent.retrieval.search", new_callable=AsyncMock, return_value=mock_results):
            result = await _fetch_memory_ctx("test query long")
        assert len(result) <= 320  # "[Test] " + 300 Zeichen


# ── _fetch_session_ctx ────────────────────────────────────────────────────────


class TestFetchSessionCtx:
    async def test_filters_by_entity_name(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        fake_sessions = "Zeile mit Salvador hier\nAndere Zeile ohne Treffer\nNochmal Salvador erwähnt"
        with patch("agent.agents.chat_agent._load_all_sessions", return_value=fake_sessions):
            result = await _fetch_session_ctx("Salvador")
        assert "Salvador" in result
        assert "Andere Zeile" not in result

    async def test_case_insensitive_filter(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        fake_sessions = "salvador kleingeschrieben\nNichts passendes"
        with patch("agent.agents.chat_agent._load_all_sessions", return_value=fake_sessions):
            result = await _fetch_session_ctx("Salvador")
        assert "salvador" in result

    async def test_no_match_returns_empty(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        fake_sessions = "Zeile A\nZeile B\nZeile C"
        with patch("agent.agents.chat_agent._load_all_sessions", return_value=fake_sessions):
            result = await _fetch_session_ctx("XYZnirgends")
        assert result == ""

    async def test_truncates_to_500_chars(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        long_line = "Salvador " + "x " * 300
        fake_sessions = "\n".join([long_line] * 10)
        with patch("agent.agents.chat_agent._load_all_sessions", return_value=fake_sessions):
            result = await _fetch_session_ctx("Salvador")
        assert len(result) <= 502  # 500 + "…"

    async def test_returns_empty_on_exception(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        with patch("agent.agents.chat_agent._load_all_sessions", side_effect=Exception("io error")):
            result = await _fetch_session_ctx("Salvador")
        assert result == ""

    async def test_returns_empty_when_sessions_empty(self):
        from agent.proactive.heartbeat import _fetch_session_ctx

        with patch("agent.agents.chat_agent._load_all_sessions", return_value=""):
            result = await _fetch_session_ctx("Salvador")
        assert result == ""


# ── _gather_heartbeat_context ─────────────────────────────────────────────────


class TestGatherHeartbeatContext:
    async def test_returns_all_three_keys(self):
        from agent.proactive.heartbeat import _gather_heartbeat_context

        with (
            patch("agent.proactive.heartbeat._fetch_profile_ctx", new_callable=AsyncMock, return_value="Fabio Berlin"),
            patch(
                "agent.proactive.heartbeat._fetch_memory_ctx", new_callable=AsyncMock, return_value="[Reise] Salvador"
            ),
            patch(
                "agent.proactive.heartbeat._fetch_session_ctx",
                new_callable=AsyncMock,
                return_value="Session: Salvador 2026",
            ),
        ):
            ctx = await _gather_heartbeat_context(TRIGGER_ITEM)
        assert ctx["profile"] == "Fabio Berlin"
        assert ctx["memory"] == "[Reise] Salvador"
        assert ctx["sessions"] == "Session: Salvador 2026"

    async def test_timeout_returns_empty_strings(self):
        from agent.proactive.heartbeat import _gather_heartbeat_context

        async def slow():
            await asyncio.sleep(10)
            return "data"

        with (
            patch("agent.proactive.heartbeat._fetch_profile_ctx", side_effect=slow),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", side_effect=lambda q: slow()),
            patch("agent.proactive.heartbeat._fetch_session_ctx", side_effect=lambda n: slow()),
            patch("agent.proactive.heartbeat.CONTEXT_FETCH_TIMEOUT", 0.1),
        ):
            ctx = await _gather_heartbeat_context(TRIGGER_ITEM)
        assert ctx == {"profile": "", "memory": "", "sessions": ""}


# ── generate_proactive_message (Prompt-Aufbau) ───────────────────────────────


class TestGenerateProactiveMessageContext:
    async def test_prompt_contains_all_three_sections(self):
        from agent.proactive.heartbeat import generate_proactive_message

        captured_prompt = {}

        async def fake_ainvoke(messages, **kwargs):
            captured_prompt["text"] = messages[0].content
            return MagicMock(content="Du solltest ein Hotel buchen.")

        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke

        with (
            patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm),
            patch(
                "agent.proactive.heartbeat._fetch_profile_ctx",
                new_callable=AsyncMock,
                return_value="User: Fabio (Berlin)",
            ),
            patch(
                "agent.proactive.heartbeat._fetch_memory_ctx",
                new_callable=AsyncMock,
                return_value="[Reise] Salvador info",
            ),
            patch(
                "agent.proactive.heartbeat._fetch_session_ctx",
                new_callable=AsyncMock,
                return_value="Session: Reise besprochen",
            ),
        ):
            result = await generate_proactive_message(TRIGGER_ITEM)

        assert "Hotel" in result
        prompt = captured_prompt["text"]
        assert "Persönliches Profil" in prompt
        assert "Relevantes Wissen" in prompt
        assert "Frühere Sessions" in prompt

    async def test_fallback_on_llm_error_with_context(self):
        from agent.proactive.heartbeat import generate_proactive_message

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM down"))

        with (
            patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm),
            patch("agent.proactive.heartbeat._fetch_profile_ctx", new_callable=AsyncMock, return_value="User: Fabio"),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_session_ctx", new_callable=AsyncMock, return_value=""),
        ):
            result = await generate_proactive_message(TRIGGER_ITEM)

        assert "Salvador" in result or "7" in result

    async def test_all_sources_empty_still_calls_llm(self):
        from agent.proactive.heartbeat import generate_proactive_message

        llm_called = {}

        async def fake_ainvoke(messages, **kwargs):
            llm_called["called"] = True
            return MagicMock(content="Hast du schon geplant?")

        mock_llm = MagicMock()
        mock_llm.ainvoke = fake_ainvoke

        with (
            patch("agent.proactive.heartbeat._get_llm", return_value=mock_llm),
            patch("agent.proactive.heartbeat._fetch_profile_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_memory_ctx", new_callable=AsyncMock, return_value=""),
            patch("agent.proactive.heartbeat._fetch_session_ctx", new_callable=AsyncMock, return_value=""),
        ):
            result = await generate_proactive_message(TRIGGER_ITEM)

        assert llm_called.get("called") is True
        assert "geplant" in result
