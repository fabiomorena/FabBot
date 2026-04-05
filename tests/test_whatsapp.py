"""
Tests für Phase 81 – WhatsApp Agent (v2 – korrekte Mock-Pfade).

Zwei Ursachen der v1-Fehler:
1. load_profile wird in load_whatsapp_contacts() per lokalem Import geladen
   → Patch-Pfad: "agent.profile.load_profile" (Quelle), nicht "bot.whatsapp.load_profile"
2. is_session_ready / find_contact / load_whatsapp_contacts werden in whatsapp_agent()
   per lokalem Import geladen
   → Patch-Pfad: "bot.whatsapp.is_session_ready" etc. (Quelle)
"""

import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import HumanMessage, AIMessage


# ---------------------------------------------------------------------------
# bot/whatsapp.py Tests
# ---------------------------------------------------------------------------

class TestIsSessionReady:
    def test_no_file(self, tmp_path):
        with patch("bot.whatsapp._SESSION_FILE", tmp_path / "nonexistent.json"):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text("")
        with patch("bot.whatsapp._SESSION_FILE", f):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is False

    def test_valid_file(self, tmp_path):
        f = tmp_path / "session.json"
        f.write_text("x" * 200)
        with patch("bot.whatsapp._SESSION_FILE", f):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is True


class TestLoadWhatsappContacts:
    """
    load_whatsapp_contacts() importiert load_profile intern.
    Patch-Pfad muss an der Quelle ansetzen: "agent.profile.load_profile"
    """

    def test_no_whatsapp_contacts_in_profile(self):
        with patch("agent.profile.load_profile", return_value={}):
            from bot.whatsapp import load_whatsapp_contacts
            assert load_whatsapp_contacts() == []

    def test_returns_contacts(self):
        profile = {
            "whatsapp_contacts": [
                {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
                {"name": "Amalia", "whatsapp_name": "Amalia"},
            ]
        }
        with patch("agent.profile.load_profile", return_value=profile):
            from bot.whatsapp import load_whatsapp_contacts
            result = load_whatsapp_contacts()
            assert len(result) == 2
            assert result[0]["name"] == "Steffi"

    def test_invalid_contacts_type(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": "invalid"}):
            from bot.whatsapp import load_whatsapp_contacts
            assert load_whatsapp_contacts() == []

    def test_profile_load_error(self):
        with patch("agent.profile.load_profile", side_effect=Exception("fail")):
            from bot.whatsapp import load_whatsapp_contacts
            assert load_whatsapp_contacts() == []


class TestFindContact:
    def _contacts(self):
        return [
            {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
            {"name": "Amalia", "whatsapp_name": "Amalia"},
            {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"},
        ]

    def test_exact_match(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": self._contacts()}):
            from bot.whatsapp import find_contact
            result = find_contact("Steffi")
            assert result is not None
            assert result["whatsapp_name"] == "Steffi 🌞"

    def test_case_insensitive(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": self._contacts()}):
            from bot.whatsapp import find_contact
            assert find_contact("steffi") is not None

    def test_not_found(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": self._contacts()}):
            from bot.whatsapp import find_contact
            assert find_contact("Jonas") is None

    def test_empty_name(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": self._contacts()}):
            from bot.whatsapp import find_contact
            assert find_contact("") is None

    def test_whitespace_stripped(self):
        with patch("agent.profile.load_profile", return_value={"whatsapp_contacts": self._contacts()}):
            from bot.whatsapp import find_contact
            assert find_contact("  Amalia  ") is not None


# ---------------------------------------------------------------------------
# agent/agents/whatsapp_agent.py Tests
#
# whatsapp_agent() importiert is_session_ready/find_contact/load_whatsapp_contacts
# per lokalem "from bot.whatsapp import ..." innerhalb der Funktion.
# Patch-Pfad: "bot.whatsapp.is_session_ready" etc. (an der Quelle)
# ---------------------------------------------------------------------------

def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": 12345,
        "next_agent": None,
    }


_CONTACTS = [
    {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
    {"name": "Amalia", "whatsapp_name": "Amalia"},
    {"name": "Fabio",  "whatsapp_name": "Fabio Morena (du)"},
]


@pytest.mark.asyncio
class TestWhatsappAgent:

    async def test_no_session(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        with patch("bot.whatsapp.is_session_ready", return_value=False):
            result = await whatsapp_agent(_state("Schick Steffi hallo"))
        content = result["messages"][-1].content
        assert "wa_setup" in content.lower() or "eingerichtet" in content.lower()

    async def test_contact_not_in_whitelist(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "Jonas", "message": "Hallo"}')
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.load_whatsapp_contacts", return_value=_CONTACTS),
            patch("bot.whatsapp.find_contact", return_value=None),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("Schick Jonas Hallo"))
        content = result["messages"][-1].content
        assert "whitelist" in content.lower() or "erlaubt" in content.lower()

    async def test_valid_contact_returns_hitl(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Ich komme später"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("Schick Steffi dass ich später komme"))
        content = result["messages"][-1].content
        assert content.startswith(Proto.CONFIRM_WHATSAPP)
        assert "Steffi 🌞" in content
        assert "Ich komme später" in content

    async def test_empty_contact_asks_user(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "", "message": "Hallo"}')
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("schick irgendjemandem hallo"))
        content = result["messages"][-1].content
        assert "kontakt" in content.lower() or "anschreiben" in content.lower()

    async def test_empty_message_asks_user(self):
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content='{"contact": "Steffi", "message": ""}')
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("schick Steffi etwas"))
        content = result["messages"][-1].content
        assert "steffi" in content.lower() or "schreiben" in content.lower()

    async def test_llm_returns_natural_language(self):
        """Phase 75 kompatibel: LLM-Rückfrage wird direkt durchgegeben."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        llm_response = AIMessage(content="Wen soll ich anschreiben?")
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("schick mal was"))
        content = result["messages"][-1].content
        assert "anschreiben" in content.lower()

    async def test_whatsapp_name_with_emoji(self):
        """whatsapp_name inkl. Emoji muss exakt im HITL-String stehen."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Test"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("Schick Steffi Test"))
        assert "Steffi 🌞" in result["messages"][-1].content

    async def test_fabio_self_send(self):
        """Selbst-Send an Fabio (Test-Kontakt) funktioniert."""
        from agent.agents.whatsapp_agent import whatsapp_agent
        from agent.protocol import Proto
        llm_response = AIMessage(content='{"contact": "Fabio", "message": "Test 123"}')
        contact = {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await whatsapp_agent(_state("Schick mir selbst Test 123"))
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

    def test_is_confirm_whatsapp_true(self):
        from agent.protocol import Proto
        assert Proto.is_confirm_whatsapp("__CONFIRM_WHATSAPP__:Steffi 🌞::Hallo") is True

    def test_is_confirm_whatsapp_false(self):
        from agent.protocol import Proto
        assert Proto.is_confirm_whatsapp("__CONFIRM_TERMINAL__:ls") is False
        assert Proto.is_confirm_whatsapp("") is False

    def test_is_any_confirm_includes_whatsapp(self):
        from agent.protocol import Proto
        assert Proto.is_any_confirm("__CONFIRM_WHATSAPP__:x::y") is True

    def test_is_any_confirm_still_covers_others(self):
        from agent.protocol import Proto
        assert Proto.is_any_confirm("__CONFIRM_TERMINAL__:ls") is True
        assert Proto.is_any_confirm("__CONFIRM_FILE_WRITE__:/tmp/x::content") is True
        assert Proto.is_any_confirm("__CONFIRM_COMPUTER__:click:0:0:") is True
