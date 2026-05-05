"""Tests für Phase 140 – Skill-Loader und zweistufiger Memory-Router."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage


# ---------------------------------------------------------------------------
# Skill-Loader Tests
# ---------------------------------------------------------------------------


class TestSkillLoader:
    def test_load_skill_exists(self):
        from agent.skills import load_skill

        content = load_skill("memory", "router")
        assert "action" in content
        assert "category" in content

    def test_load_skill_not_found(self):
        from agent.skills import load_skill

        with pytest.raises(FileNotFoundError):
            load_skill("memory", "nonexistent_xyz")

    def test_load_skill_resolves_includes(self):
        from agent.skills import load_skill

        content = load_skill("memory", "router")
        assert "{{include:" not in content
        assert "SICHERHEIT" in content

    def test_load_skill_lru_cache(self):
        from agent.skills import load_skill

        load_skill.cache_clear()
        load_skill("memory", "people")
        load_skill("memory", "people")
        info = load_skill.cache_info()
        assert info.hits >= 1

    def test_all_skill_files_loadable(self):
        from agent.skills import load_skill

        categories = [
            "router",
            "people",
            "project",
            "place",
            "media",
            "preference",
            "job",
            "location",
            "custom",
            "bot_instruction",
        ]
        for cat in categories:
            content = load_skill("memory", cat)
            assert len(content) > 50, f"Skill {cat} ist zu kurz"

    def test_shared_security_included_in_all_skills(self):
        from agent.skills import load_skill

        categories = [
            "router",
            "people",
            "project",
            "place",
            "media",
            "preference",
            "job",
            "location",
            "custom",
            "bot_instruction",
        ]
        for cat in categories:
            content = load_skill("memory", cat)
            assert "SICHERHEIT" in content, f"Skill {cat} enthält keinen Security-Block"


# ---------------------------------------------------------------------------
# Router Tests (_route_memory_category)
# ---------------------------------------------------------------------------


def _mock_llm_response(text: str):
    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = text
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    return mock_llm


@pytest.mark.asyncio
class TestMemoryRouter:
    async def test_routing_people(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"people"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Mein Kollege Bob aus Berlin")])
        assert result["category"] == "people"
        assert result["action"] == "save"

    async def test_routing_place(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"place"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Beste Pasta beim Sissi in Neukölln")])
        assert result["category"] == "place"

    async def test_routing_media(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"media"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Höre gerade Aphex Twin")])
        assert result["category"] == "media"

    async def test_routing_job(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"job"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Ich arbeite jetzt bei Foo GmbH")])
        assert result["category"] == "job"

    async def test_routing_bot_instruction(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"bot_instruction"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Antworte mir grundsätzlich kurz")])
        assert result["category"] == "bot_instruction"

    async def test_routing_delete_action(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"delete","category":"people"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Lösch Bob")])
        assert result["action"] == "delete"

    async def test_routing_clarify_action(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"clarify","category":"preference"}')
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="Lösch das irgendwas")])
        assert result["action"] == "clarify"

    async def test_routing_invalid_json_returns_error(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response("das ist kein json")
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category([HumanMessage(content="test")])
        assert result["action"] == "error"

    async def test_routing_skips_confirm_messages(self):
        from agent.agents.memory_agent import _route_memory_category

        mock_llm = _mock_llm_response('{"action":"save","category":"people"}')
        messages = [
            HumanMessage(content="__CONFIRM_TERMINAL__: some command"),
            HumanMessage(content="Mein Freund Klaus"),
        ]
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=mock_llm):
            result = await _route_memory_category(messages)
        assert result["action"] != "error"


# ---------------------------------------------------------------------------
# _parse_memory_intent Integration (zweistufig)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestParseMemoryIntent:
    async def test_two_stage_people(self):
        from agent.agents.memory_agent import _parse_memory_intent

        router_resp = _mock_llm_response('{"action":"save","category":"people"}')
        extractor_resp = _mock_llm_response(
            '{"action":"save","category":"people","data":{"name":"Bob","context":"Kollege"}}'
        )
        with (
            patch("agent.agents.memory_agent.get_fast_llm", return_value=router_resp),
            patch("agent.agents.memory_agent.get_llm", return_value=extractor_resp),
        ):
            result = await _parse_memory_intent([HumanMessage(content="Mein Kollege Bob")])
        assert result["action"] == "save"
        assert result["category"] == "people"
        assert result["data"]["name"] == "Bob"

    async def test_router_error_returns_error(self):
        from agent.agents.memory_agent import _parse_memory_intent

        router_resp = _mock_llm_response("kein json")
        with patch("agent.agents.memory_agent.get_fast_llm", return_value=router_resp):
            result = await _parse_memory_intent([HumanMessage(content="test")])
        assert result["action"] == "error"

    async def test_missing_skill_file_returns_error(self, tmp_path, monkeypatch):
        from agent.agents.memory_agent import _parse_memory_intent

        router_resp = _mock_llm_response('{"action":"save","category":"unknown_cat"}')
        with (
            patch("agent.agents.memory_agent.get_fast_llm", return_value=router_resp),
            patch("agent.agents.memory_agent.load_skill", side_effect=FileNotFoundError("fehlt")),
        ):
            result = await _parse_memory_intent([HumanMessage(content="test")])
        assert result["action"] == "error"

    async def test_category_injected_when_missing(self):
        from agent.agents.memory_agent import _parse_memory_intent

        router_resp = _mock_llm_response('{"action":"save","category":"place"}')
        extractor_resp = _mock_llm_response(
            '{"action":"save","data":{"name":"Berghain","type":"bar","location":"Berlin","context":""}}'
        )
        with (
            patch("agent.agents.memory_agent.get_fast_llm", return_value=router_resp),
            patch("agent.agents.memory_agent.get_llm", return_value=extractor_resp),
        ):
            result = await _parse_memory_intent([HumanMessage(content="Berghain ist ein Club")])
        assert result["category"] == "place"
