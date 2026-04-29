"""
tests/test_ph144_multi_agent_briefing.py – Phase 144 (Issue #91)

Testet das Multi-Agent Briefing:
- _run_with_timeout: Timeout → Fallback, Exception → Fallback, Erfolg → Result
- orchestrate_briefing: alle Sub-Agenten parallel, Ausfall blockiert nicht
- generate_briefing nutzt orchestrate_briefing
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch


class TestRunWithTimeout:
    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        from agent.proactive.briefing_agent import _run_with_timeout

        async def fast():
            return "ok"

        result = await _run_with_timeout(fast(), fallback="fail", name="test")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_timeout(self):
        from agent.proactive.briefing_agent import _run_with_timeout

        async def slow():
            await asyncio.sleep(10)
            return "never"

        result = await _run_with_timeout(slow(), fallback="fallback", name="test", timeout=0.05)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_fallback_on_exception(self):
        from agent.proactive.briefing_agent import _run_with_timeout

        async def broken():
            raise RuntimeError("boom")

        result = await _run_with_timeout(broken(), fallback="fallback", name="test")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_does_not_raise(self):
        from agent.proactive.briefing_agent import _run_with_timeout

        async def broken():
            raise ValueError("error")

        result = await _run_with_timeout(broken(), fallback="safe", name="test")
        assert result == "safe"


class TestOrchestrateBriefing:
    @pytest.mark.asyncio
    async def test_returns_all_sections(self):
        from agent.proactive.briefing_agent import orchestrate_briefing

        async def w():
            return "Sonnig"

        async def c():
            return "Kein Termin"

        async def p():
            return "✅ Reise planen"

        async def n():
            return "KI-News"

        result = await orchestrate_briefing(weather_fn=w, calendar_fn=c, pending_fn=p, news_fn=n)
        assert result["weather"] == "Sonnig"
        assert result["calendar"] == "Kein Termin"
        assert result["pending"] == "✅ Reise planen"
        assert result["news"] == "KI-News"

    @pytest.mark.asyncio
    async def test_failing_agent_gets_fallback(self):
        from agent.proactive.briefing_agent import orchestrate_briefing

        async def w():
            raise Exception("Wetter kaputt")

        async def c():
            return "Termine"

        async def p():
            return ""

        async def n():
            return "News"

        result = await orchestrate_briefing(weather_fn=w, calendar_fn=c, pending_fn=p, news_fn=n)
        assert result["weather"] != ""  # Fallback-Text vorhanden
        assert result["calendar"] == "Termine"

    @pytest.mark.asyncio
    async def test_timeout_agent_does_not_block_others(self):
        from agent.proactive.briefing_agent import orchestrate_briefing

        async def w():
            await asyncio.sleep(10)
            return "never"

        async def c():
            return "Termine"

        async def p():
            return ""

        async def n():
            return "News"

        result = await asyncio.wait_for(
            orchestrate_briefing(weather_fn=w, calendar_fn=c, pending_fn=p, news_fn=n, timeout=0.05), timeout=2.0
        )
        assert result["calendar"] == "Termine"
        assert result["weather"] != "never"

    @pytest.mark.asyncio
    async def test_all_agents_run_in_parallel(self):
        from agent.proactive.briefing_agent import orchestrate_briefing
        import time

        calls = []

        async def w():
            calls.append(("weather", time.monotonic()))
            await asyncio.sleep(0.05)
            return "W"

        async def c():
            calls.append(("calendar", time.monotonic()))
            await asyncio.sleep(0.05)
            return "C"

        async def p():
            calls.append(("pending", time.monotonic()))
            return ""

        async def n():
            calls.append(("news", time.monotonic()))
            await asyncio.sleep(0.05)
            return "N"

        start = time.monotonic()
        await orchestrate_briefing(weather_fn=w, calendar_fn=c, pending_fn=p, news_fn=n)
        elapsed = time.monotonic() - start

        assert elapsed < 0.2  # parallel: max ~0.05s, nicht sequentiell 0.15s+
        assert len(calls) == 4


class TestGenerateBriefingUsesOrchestrator:
    @pytest.mark.asyncio
    async def test_generate_briefing_uses_orchestrate(self):
        from bot.briefing import generate_briefing

        mock_result = {
            "weather": "Sonnig",
            "calendar": "Kein Termin",
            "pending": "",
            "news": "Keine News",
        }

        with patch("bot.briefing.orchestrate_briefing", new_callable=AsyncMock, return_value=mock_result):
            result = await generate_briefing()

        assert "Guten Morgen" in result
        assert "Sonnig" in result
        assert "Kein Termin" in result
