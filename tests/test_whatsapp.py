"""
Tests für Phase 81 – WhatsApp Agent.

Kein Playwright, kein echtes WhatsApp – alles gemockt.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage


# ---------------------------------------------------------------------------
# bot/whatsapp.py Tests
# ---------------------------------------------------------------------------

class TestIsSessionReady:
    def test_no_file(self, tmp_path):
        from bot.whatsapp import _STATUS_FILE
        with patch("bot.whatsapp._STATUS_FILE", tmp_path / "nonexistent.json"):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text("")
        with patch("bot.whatsapp._STATUS_FILE", f):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is False

    def test_valid_file(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text("x" * 200)
        with patch("bot.whatsapp._STATUS_FILE", f):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is True


class TestLoadWhatsappContacts:
    def test_no_whatsapp_contacts_in_profile(self):
        with patch("bot.whatsapp.load_profile", return_value={}):
            from bot.whatsapp import load_whatsapp_contacts
            result = load_whatsapp_contacts()
            assert result == []

    def test_returns_contacts(self):
        profile = {
            "whatsapp_contacts": [
                {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
                {"name": "Amalia", "whatsapp_name": "Amalia"},
            ]
        }
        with patch("bot.whatsapp.load_profile", return_value=profile):
            from bot.whatsapp import load_whatsapp_contacts
            result = load_whatsapp_contacts()
            assert len(result) == 2
            assert result[0]["name"] == "Steffi"

    def test_invalid_contacts_type(self):
        with patch("bot.whatsapp.load_profile", return_value={"whatsapp_contacts": "invalid"}):
            from bot.whatsapp import load_whatsapp_contacts
            result = load_whatsapp_contacts()
            assert result == []

    def test_profile_load_error(self):
        with patch("bot.whatsapp.load_profile", side_effect=Exception("fail")):
            from bot.whatsapp import load_whatsapp_contacts
            result = load_whatsapp_contacts()
            assert result == []


class TestFindContact:
    def _mock_contacts(self):
        return [
            {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
            {"name": "Amalia", "whatsapp_name": "Amalia"},
            {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"},
        ]

    def test_exact_match(self):
        with patch("bot.whatsapp.load_whatsapp_contacts", return_value=self._mock_contacts()):
            from bot.whatsapp import find_contact
            result = find_contact("Steffi")
            assert result is not None
            assert result["whatsapp_name"] == "Steffi 🌞"

    def test_case_insensitive(self):
        with patch("bot.whatsapp.load_whatsapp_contacts", return_value=self._mock_contacts()):
            from bot.whatsapp import find_contact
            result = find_contact("steffi")
            assert result is not None

    def test_not_found(self):
        with patch("bot.whatsapp.load_whatsapp_contacts", return_value=self._mock_contacts()):
            from bot.whatsapp import find_contact
            result = find_contact("Jonas")
            assert result is None

    def test_empty_name(self):
        with patch("bot.whatsapp.load_whatsapp_contacts", return_value=self._mock_contacts()):
            from bot.whatsapp import find_contact
            result = find_contact("")
            assert result is None

    def test_whitespace_stripped(self):
        with patch("bot.whatsapp.load_whatsapp_contacts", return_value=self._mock_contacts()):
            from bot.whatsapp import find_contact
            result = find_contact("  Amalia  ")
            assert result is not None


# ---------------------------------------------------------------------------
# agent/agents/whatsapp_agent.py Tests
# ---------------------------------------------------------------------------

def _make_state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": 12345,
        "next_agent": None,
    }


_MOCK_CONTACTS = [
    {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
    {"name": "Amalia", "whatsapp_name": "Amalia"},
    {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"},
]


@pytest.mark.asyncio
class TestWhatsappAgent:

    async def test_no_session(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        with patch("agent.agents.whatsapp_agent.is_session_ready", return_value=False):
            result = await whatsapp_agent(_make_state("Schick Steffi hallo"))
        content = result["messages"][-1].content
        assert "wa_setup" in content.lower() or "eingerichtet" in content.lower()

    async def test_contact_not_in_whitelist(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "Jonas", "message": "Hallo"}')
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("agent.agents.whatsapp_agent.load_whatsapp_contacts", return_value=_MOCK_CONTACTS),
            patch("agent.agents.whatsapp_agent.find_contact", return_value=None),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("Schick Jonas Hallo"))
        content = result["messages"][-1].content
        assert "whitelist" in content.lower() or "erlaubt" in content.lower()

    async def test_valid_contact_returns_hitl(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Ich komme später"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("agent.agents.whatsapp_agent.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("Schick Steffi dass ich später komme"))
        content = result["messages"][-1].content
        assert content.startswith(Proto.CONFIRM_WHATSAPP)
        assert "Steffi 🌞" in content
        assert "Ich komme später" in content

    async def test_empty_contact_asks_user(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "", "message": "Hallo"}')
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("schick irgendjemandem hallo"))
        content = result["messages"][-1].content
        assert "kontakt" in content.lower() or "anschreiben" in content.lower()

    async def test_empty_message_asks_user(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "Steffi", "message": ""}')
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("schick Steffi etwas"))
        content = result["messages"][-1].content
        assert "steffi" in content.lower() or "schreiben" in content.lower()

    async def test_llm_returns_natural_language(self):
        """Phase 75 kompatibel: LLM-Rückfrage wird durchgegeben."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content="Wen soll ich anschreiben?")
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("schick mal was"))
        content = result["messages"][-1].content
        assert "anschreiben" in content.lower()

    async def test_whatsapp_name_with_emoji(self):
        """Stellt sicher dass whatsapp_name (inkl. Emoji) korrekt weitergegeben wird."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Test"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("agent.agents.whatsapp_agent.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("Schick Steffi Test"))
        content = result["messages"][-1].content
        # whatsapp_name muss exakt mit Emoji übergeben werden
        assert "Steffi 🌞" in content

    async def test_fabio_self_send(self):
        """Test-Kontakt Fabio (self-send) funktioniert."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Fabio", "message": "Test 123"}')
        contact = {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"}
        with (
            patch("agent.agents.whatsapp_agent.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("agent.agents.whatsapp_agent.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_make_state("Schick mir selbst Test 123"))
        content = result["messages"][-1].content
        assert content.startswith(Proto.CONFIRM_WHATSAPP)
        assert "Fabio Morena (du)" in content


# ---------------------------------------------------------------------------
# Protocol Tests
# ---------------------------------------------------------------------------

class TestProtoWhatsapp:
    def test_confirm_whatsapp_prefix(self):
        from agent.protocol import Proto
        assert Proto.CONFIRM_WHATSAPP == "__CONFIRM_WHATSAPP__:"

    def test_is_confirm_whatsapp(self):
        from agent.protocol import Proto
        assert Proto.is_confirm_whatsapp("__CONFIRM_WHATSAPP__:Steffi::Hallo") is True
        assert Proto.is_confirm_whatsapp("__CONFIRM_TERMINAL__:ls") is False
        assert Proto.is_confirm_whatsapp("") is False

    def test_is_any_confirm_includes_whatsapp(self):
        from agent.protocol import Proto
        assert Proto.is_any_confirm("__CONFIRM_WHATSAPP__:x::y") is True
