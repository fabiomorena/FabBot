"""
Tests für Phase 83 – WhatsApp Service (HTTP-basiert, whatsapp-web.js).

Änderungen gegenüber Phase 81:
- TestIsSessionReady: prüft jetzt _STATUS_FILE statt _SESSION_FILE
- Neu: TestGetServiceStatus, TestGetQrCode, TestSendWhatsappMessageHttp
- Neu: TestStartService
- TestLoadWhatsappContacts, TestFindContact, TestWhatsappAgent,
  TestProtoWhatsapp, TestAddWhatsappContact, TestRemoveWhatsappContact
  → unverändert
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage


# ---------------------------------------------------------------------------
# is_session_ready() – prüft jetzt _STATUS_FILE (~/. fabbot/wa_ready)
# ---------------------------------------------------------------------------

class TestIsSessionReady:
    def test_no_status_file(self, tmp_path):
        """Keine Status-Datei → False."""
        with patch("bot.whatsapp._STATUS_FILE", tmp_path / "wa_ready"):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is False

    def test_status_file_exists(self, tmp_path):
        """Status-Datei vorhanden → True."""
        status_file = tmp_path / "wa_ready"
        status_file.write_text("1")
        with patch("bot.whatsapp._STATUS_FILE", status_file):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is True

    def test_status_file_empty_still_true(self, tmp_path):
        """Leere Status-Datei existiert → True (nur Existenz zählt)."""
        status_file = tmp_path / "wa_ready"
        status_file.write_text("")
        with patch("bot.whatsapp._STATUS_FILE", status_file):
            from bot.whatsapp import is_session_ready
            assert is_session_ready() is True


# ---------------------------------------------------------------------------
# get_service_status()
# ---------------------------------------------------------------------------

class TestGetServiceStatus:
    @pytest.mark.asyncio
    async def test_service_ready(self):
        """Service antwortet mit ready=True."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True, "ready": True, "qr_available": False, "error": None
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.get        = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_service_status
            result = await get_service_status()

        assert result["ok"]    is True
        assert result["ready"] is True

    @pytest.mark.asyncio
    async def test_service_qr_available(self):
        """Service gibt qr_available=True zurück."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True, "ready": False, "qr_available": True, "error": None
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.get        = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_service_status
            result = await get_service_status()

        assert result["qr_available"] is True
        assert result["ready"]        is False

    @pytest.mark.asyncio
    async def test_service_unreachable(self):
        """Verbindungsfehler → ok=False, ready=False, kein Crash."""
        with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
            from bot.whatsapp import get_service_status
            result = await get_service_status()

        assert result["ok"]    is False
        assert result["ready"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_service_timeout(self):
        """Timeout → ok=False, fail-safe."""
        import httpx
        with patch("httpx.AsyncClient", side_effect=httpx.TimeoutException("timeout")):
            from bot.whatsapp import get_service_status
            result = await get_service_status()

        assert result["ok"]    is False
        assert result["ready"] is False


# ---------------------------------------------------------------------------
# get_qr_code()
# ---------------------------------------------------------------------------

class TestGetQrCode:
    @pytest.mark.asyncio
    async def test_qr_returned_when_available(self):
        """QR-Code-String wird zurückgegeben."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "qr": "fake_qr_string_12345"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.get        = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_qr_code
            result = await get_qr_code()

        assert result == "fake_qr_string_12345"

    @pytest.mark.asyncio
    async def test_qr_none_when_not_available(self):
        """404 vom Service → None zurückgegeben."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.get        = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_qr_code
            result = await get_qr_code()

        assert result is None

    @pytest.mark.asyncio
    async def test_qr_none_on_exception(self):
        """Exception → None, kein Crash (fail-safe)."""
        with patch("httpx.AsyncClient", side_effect=Exception("conn error")):
            from bot.whatsapp import get_qr_code
            result = await get_qr_code()

        assert result is None


# ---------------------------------------------------------------------------
# send_whatsapp_message() – HTTP-basiert
# ---------------------------------------------------------------------------

class TestSendWhatsappMessageHttp:
    @pytest.mark.asyncio
    async def test_send_success(self):
        """Erfolgreiches Senden → (True, detail)."""
        send_resp = MagicMock()
        send_resp.json.return_value = {"ok": True, "detail": "✅ Gesendet an Steffi 🌞"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.post       = AsyncMock(return_value=send_resp)

        with patch("bot.whatsapp.is_session_ready", return_value=True), \
             patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import send_whatsapp_message
            success, detail = await send_whatsapp_message("Steffi 🌞", "Hallo!")

        assert success is True
        assert "Gesendet" in detail

    @pytest.mark.asyncio
    async def test_send_contact_not_found(self):
        """Node.js meldet Kontakt nicht gefunden → (False, Fehlermeldung)."""
        send_resp = MagicMock()
        send_resp.json.return_value = {
            "ok": False,
            "error": "Kontakt 'Unknown' nicht in WhatsApp gefunden."
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__  = AsyncMock(return_value=None)
        mock_client.post       = AsyncMock(return_value=send_resp)

        with patch("bot.whatsapp.is_session_ready", return_value=True), \
             patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import send_whatsapp_message
            success, detail = await send_whatsapp_message("Unknown", "Text")

        assert success is False
        assert "gefunden" in detail.lower() or "Unknown" in detail

    @pytest.mark.asyncio
    async def test_send_fails_when_not_ready(self):
        """Nicht verbunden → (False, Hinweis auf wa_setup)."""
        with patch("bot.whatsapp.is_session_ready", return_value=False), \
             patch("bot.whatsapp.get_service_status", new_callable=AsyncMock,
                   return_value={"ok": True, "ready": False}):
            from bot.whatsapp import send_whatsapp_message
            success, detail = await send_whatsapp_message("Steffi 🌞", "Hallo!")

        assert success is False
        assert "wa_setup" in detail.lower() or "verbunden" in detail.lower()

    @pytest.mark.asyncio
    async def test_send_timeout_returns_false(self):
        """HTTP-Timeout → (False, Timeout-Meldung)."""
        import httpx
        with patch("bot.whatsapp.is_session_ready", return_value=True), \
             patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__  = AsyncMock(return_value=None)
            mock_ctx.post       = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_cls.return_value = mock_ctx

            from bot.whatsapp import send_whatsapp_message
            success, detail = await send_whatsapp_message("Steffi 🌞", "Text")

        assert success is False
        assert "timeout" in detail.lower() or "Timeout" in detail

    @pytest.mark.asyncio
    async def test_send_exception_returns_false(self):
        """Unerwartete Exception → (False, Fehlermeldung), kein Crash."""
        with patch("bot.whatsapp.is_session_ready", return_value=True), \
             patch("httpx.AsyncClient", side_effect=Exception("network down")):
            from bot.whatsapp import send_whatsapp_message
            success, detail = await send_whatsapp_message("Steffi 🌞", "Text")

        assert success is False


# ---------------------------------------------------------------------------
# start_service() / stop_service()
# ---------------------------------------------------------------------------

class TestServiceLifecycle:
    @pytest.mark.asyncio
    async def test_start_service_no_node(self):
        """Node.js nicht in PATH → False, kein Crash."""
        with patch("shutil.which", return_value=None):
            from bot.whatsapp import start_service
            result = await start_service()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_no_server_js(self, tmp_path):
        """server.js nicht vorhanden → False."""
        with patch("shutil.which", return_value="/usr/bin/node"), \
             patch("bot.whatsapp._NODE_SERVICE", tmp_path / "nonexistent.js"):
            from bot.whatsapp import start_service
            result = await start_service()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_no_node_modules(self, tmp_path):
        """node_modules fehlt → False + Warning."""
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        with patch("shutil.which", return_value="/usr/bin/node"), \
             patch("bot.whatsapp._NODE_SERVICE", server_js):
            from bot.whatsapp import start_service
            result = await start_service()
        assert result is False

    def test_stop_service_no_process(self):
        """stop_service() ohne laufenden Prozess → kein Crash."""
        import bot.whatsapp as wa_module
        wa_module._service_process = None
        from bot.whatsapp import stop_service
        stop_service()  # darf nicht crashen

    def test_stop_service_running_process(self):
        """stop_service() mit laufendem Prozess → terminate() aufgerufen."""
        import bot.whatsapp as wa_module
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # läuft noch
        wa_module._service_process = mock_proc

        from bot.whatsapp import stop_service
        stop_service()

        mock_proc.terminate.assert_called_once()
        assert wa_module._service_process is None


# ---------------------------------------------------------------------------
# load_whatsapp_contacts()
# ---------------------------------------------------------------------------

class TestLoadWhatsappContacts:
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


# ---------------------------------------------------------------------------
# find_contact()
# ---------------------------------------------------------------------------

class TestFindContact:
    def _contacts(self):
        return [
            {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
            {"name": "Amalia", "whatsapp_name": "Amalia"},
            {"name": "Fabio",  "whatsapp_name": "Fabio Morena (du)"},
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
# whatsapp_agent – unverändert aus Phase 81 (Mocks auf gleiche Pfade)
# ---------------------------------------------------------------------------

def _state(text: str) -> dict:
    return {
        "messages":       [HumanMessage(content=text)],
        "telegram_chat_id": 12345,
        "next_agent":     None,
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
# Protocol
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
