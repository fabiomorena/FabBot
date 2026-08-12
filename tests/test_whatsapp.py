"""
Tests für Phase 83 – WhatsApp Service (HTTP-basiert, whatsapp-web.js).

Änderungen gegenüber Phase 81:
- TestIsSessionReady: prüft jetzt _STATUS_FILE statt _SESSION_FILE
- Neu: TestGetServiceStatus, TestGetQrCode, TestSendWhatsappMessageHttp
- Neu: TestStartService
- TestLoadWhatsappContacts, TestFindContact, TestWhatsappAgent,
  TestProtoWhatsapp, TestAddWhatsappContact, TestRemoveWhatsappContact
  → unverändert

Phase 95c Fix (Issue #7): TestServiceLifecycle.test_stop_service_no_process
und test_stop_service_running_process auf async umgestellt, da stop_service()
jetzt async def ist.
"""

import subprocess

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
        mock_resp.json.return_value = {"ok": True, "ready": True, "qr_available": False, "error": None}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_service_status

            result = await get_service_status()

        assert result["ok"] is True
        assert result["ready"] is True

    @pytest.mark.asyncio
    async def test_service_qr_available(self):
        """Service gibt qr_available=True zurück."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ready": False, "qr_available": True, "error": None}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from bot.whatsapp import get_service_status

            result = await get_service_status()

        assert result["qr_available"] is True
        assert result["ready"] is False

    @pytest.mark.asyncio
    async def test_service_unreachable(self):
        """Verbindungsfehler → ok=False, ready=False, kein Crash."""
        with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
            from bot.whatsapp import get_service_status

            result = await get_service_status()

        assert result["ok"] is False
        assert result["ready"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_service_timeout(self):
        """Timeout → ok=False, fail-safe."""
        import httpx

        with patch("httpx.AsyncClient", side_effect=httpx.TimeoutException("timeout")):
            from bot.whatsapp import get_service_status

            result = await get_service_status()

        assert result["ok"] is False
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
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

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
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

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
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=send_resp)

        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from bot.whatsapp import send_whatsapp_message

            success, detail = await send_whatsapp_message("Steffi 🌞", "Hallo!")

        assert success is True
        assert "Gesendet" in detail

    @pytest.mark.asyncio
    async def test_send_contact_not_found(self):
        """Node.js meldet Kontakt nicht gefunden → (False, Fehlermeldung)."""
        send_resp = MagicMock()
        send_resp.json.return_value = {"ok": False, "error": "Kontakt 'Unknown' nicht in WhatsApp gefunden."}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=send_resp)

        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from bot.whatsapp import send_whatsapp_message

            success, detail = await send_whatsapp_message("Unknown", "Text")

        assert success is False
        assert "gefunden" in detail.lower() or "Unknown" in detail

    @pytest.mark.asyncio
    async def test_send_fails_when_not_ready(self):
        """Nicht verbunden → (False, Hinweis auf wa_setup)."""
        with (
            patch("bot.whatsapp.is_session_ready", return_value=False),
            patch("bot.whatsapp.get_service_status", new_callable=AsyncMock, return_value={"ok": True, "ready": False}),
        ):
            from bot.whatsapp import send_whatsapp_message

            success, detail = await send_whatsapp_message("Steffi 🌞", "Hallo!")

        assert success is False
        assert "wa_setup" in detail.lower() or "verbunden" in detail.lower()

    @pytest.mark.asyncio
    async def test_send_timeout_returns_false(self):
        """HTTP-Timeout → (False, Timeout-Meldung)."""
        import httpx

        with patch("bot.whatsapp.is_session_ready", return_value=True), patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_ctx.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_cls.return_value = mock_ctx

            from bot.whatsapp import send_whatsapp_message

            success, detail = await send_whatsapp_message("Steffi 🌞", "Text")

        assert success is False
        assert "timeout" in detail.lower() or "Timeout" in detail

    @pytest.mark.asyncio
    async def test_send_exception_returns_false(self):
        """Unerwartete Exception → (False, Fehlermeldung), kein Crash."""
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("httpx.AsyncClient", side_effect=Exception("network down")),
        ):
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
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", tmp_path / "nonexistent.js"),
        ):
            from bot.whatsapp import start_service

            result = await start_service()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_no_node_modules(self, tmp_path):
        """node_modules fehlt → False + Warning."""
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        with patch("shutil.which", return_value="/usr/bin/node"), patch("bot.whatsapp._NODE_SERVICE", server_js):
            from bot.whatsapp import start_service

            result = await start_service()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_service_port_belegt_gesunder_service(self, tmp_path):
        """Port belegt + /status antwortet → True, kein zweiter Spawn.

        Tritt nach jedem Bot-Neustart auf: der Service läuft noch aus der
        vorigen Bot-Instanz. _service_process ist dann None (In-Memory-
        Referenz weg), der Port aber belegt.
        """
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._is_port_in_use", return_value=True),
            patch("bot.whatsapp.get_service_status", AsyncMock(return_value={"ok": True})),
            patch("subprocess.Popen") as mock_popen,
        ):
            result = await wa_module.start_service()

        assert result is True
        mock_popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_service_port_belegt_zombie(self, tmp_path):
        """Port belegt + /status tot → False, kein Spawn.

        Der Fall vom 11.08.2026: ein verwaister Node-Prozess hielt Port 8767,
        jeder Startversuch starb sofort und meldete irreführend
        'Check whatsapp_service/node_modules'.
        """
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._is_port_in_use", return_value=True),
            patch("bot.whatsapp.get_service_status", AsyncMock(side_effect=Exception("connection refused"))),
            patch("subprocess.Popen") as mock_popen,
        ):
            result = await wa_module.start_service()

        assert result is False
        mock_popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_service_zombie_wird_geraeumt(self, tmp_path):
        """Eigener verwaister server.js auf dem Port → wird beendet, dann Spawn."""
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        # Port erst belegt (Zombie), nach dem Räumen frei
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._is_port_in_use", side_effect=[True, False]),
            # 1. Aufruf: Zombie-Check → tot. 2. Aufruf: Polling nach dem Spawn → ok.
            patch("bot.whatsapp.get_service_status", AsyncMock(side_effect=[Exception("tot"), {"ok": True}])),
            patch("bot.whatsapp._find_orphan_service_pid", return_value=4711),
            patch("bot.whatsapp._terminate_orphan", return_value=True) as mock_term,
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            result = await wa_module.start_service()

        mock_term.assert_called_once_with(4711)
        assert result is True

    @pytest.mark.asyncio
    async def test_start_service_fremder_prozess_wird_nicht_gekillt(self, tmp_path):
        """Fremder Dienst auf dem Port → kein Kill, kein Spawn, False."""
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._is_port_in_use", return_value=True),
            patch("bot.whatsapp.get_service_status", AsyncMock(side_effect=Exception("tot"))),
            patch("bot.whatsapp._find_orphan_service_pid", return_value=None),
            patch("bot.whatsapp._terminate_orphan") as mock_term,
            patch("subprocess.Popen") as mock_popen,
        ):
            result = await wa_module.start_service()

        mock_term.assert_not_called()
        mock_popen.assert_not_called()
        assert result is False

    def test_find_orphan_pid_nur_bei_passender_cmdline_und_port(self, tmp_path):
        """Nur ein eigener server.js, der auf dem Port lauscht, wird gemeldet."""
        import psutil

        import bot.whatsapp as wa_module

        server_js = tmp_path / "server.js"

        def _proc(pid, cmdline, listen_port):
            p = MagicMock()
            p.info = {"pid": pid, "cmdline": cmdline}
            conn = MagicMock()
            conn.status = psutil.CONN_LISTEN
            conn.laddr.port = listen_port
            p.net_connections.return_value = [conn]
            return p

        fremd = _proc(1, ["/usr/bin/postgres", "-D", "/data"], 8767)  # fremd, hält Port
        falscher_port = _proc(2, ["node", str(server_js)], 9999)  # unserer, anderer Port
        treffer = _proc(3, ["node", str(server_js)], 8767)  # unserer, unser Port

        with (
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("psutil.process_iter", return_value=[fremd, falscher_port, treffer]),
        ):
            assert wa_module._find_orphan_service_pid(8767) == 3

    def test_find_orphan_pid_ignoriert_fremden_dienst(self, tmp_path):
        """Hält ein fremder Dienst den Port, wird None gemeldet (kein Kill)."""
        import psutil

        import bot.whatsapp as wa_module

        p = MagicMock()
        p.info = {"pid": 99, "cmdline": ["/usr/local/bin/redis-server", "*:8767"]}
        conn = MagicMock()
        conn.status = psutil.CONN_LISTEN
        conn.laddr.port = 8767
        p.net_connections.return_value = [conn]

        with (
            patch("bot.whatsapp._NODE_SERVICE", tmp_path / "server.js"),
            patch("psutil.process_iter", return_value=[p]),
        ):
            assert wa_module._find_orphan_service_pid(8767) is None

    def test_terminate_orphan_beendet_auch_kindprozesse(self):
        """Kindprozesse (puppeteer-Chrome) müssen mit sterben.

        Sonst hält ein verwaister Chrome den SingletonLock der Session und
        der frisch gespawnte Service scheitert erneut.
        """
        import bot.whatsapp as wa_module

        kind = MagicMock()
        parent = MagicMock()
        parent.children.return_value = [kind]

        with (
            patch("psutil.Process", return_value=parent),
            patch("psutil.wait_procs", return_value=([], [])),
        ):
            assert wa_module._terminate_orphan(4711) is True

        parent.terminate.assert_called_once()
        kind.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_service_schreibt_node_log(self, tmp_path):
        """stdout/stderr des Node-Prozesses landen in einer Logdatei, nicht in DEVNULL.

        Ohne diese Logs war am 12.08.2026 nicht feststellbar, warum der Service
        auf ready:false hing – es gab schlicht keine Spur.
        """
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        log_path = tmp_path / "whatsapp_service.log"
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._SERVICE_LOG_PATH", log_path),
            patch("bot.whatsapp._is_port_in_use", return_value=False),
            patch("bot.whatsapp.get_service_status", AsyncMock(return_value={"ok": True})),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = await wa_module.start_service()

        assert result is True
        kwargs = mock_popen.call_args.kwargs
        assert kwargs["stdout"] is not subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.STDOUT
        assert log_path.exists()

    @pytest.mark.asyncio
    async def test_service_log_wird_bei_ueberlaenge_gekuerzt(self, tmp_path):
        """Zu große Logdatei wird beim Start gekürzt – kein unbegrenztes Wachstum."""
        server_js = tmp_path / "server.js"
        server_js.write_text("// stub")
        (tmp_path / "node_modules").mkdir()
        log_path = tmp_path / "whatsapp_service.log"
        log_path.write_bytes(b"x" * 2048)
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("bot.whatsapp._NODE_SERVICE", server_js),
            patch("bot.whatsapp._SERVICE_LOG_PATH", log_path),
            patch("bot.whatsapp._SERVICE_LOG_MAX_BYTES", 1024),
            patch("bot.whatsapp._is_port_in_use", return_value=False),
            patch("bot.whatsapp.get_service_status", AsyncMock(return_value={"ok": True})),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            await wa_module.start_service()

        assert log_path.stat().st_size < 2048

    def test_is_port_in_use_freier_port(self):
        """Ein nicht gebundener Port wird als frei erkannt."""
        import socket

        from bot.whatsapp import _is_port_in_use

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            freier_port = s.getsockname()[1]
        # Socket ist hier wieder zu → Port frei
        assert _is_port_in_use(freier_port) is False

    def test_is_port_in_use_belegter_port(self):
        """Ein gebundener, lauschender Port wird als belegt erkannt."""
        import socket

        from bot.whatsapp import _is_port_in_use

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            assert _is_port_in_use(s.getsockname()[1]) is True

    @pytest.mark.asyncio
    async def test_stop_service_no_process(self):
        """stop_service() ohne laufenden Prozess → kein Crash."""
        import bot.whatsapp as wa_module

        wa_module._service_process = None
        from bot.whatsapp import stop_service

        await stop_service()  # darf nicht crashen

    @pytest.mark.asyncio
    async def test_stop_service_running_process(self):
        """stop_service() mit laufendem Prozess → terminate() aufgerufen."""
        import bot.whatsapp as wa_module

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # läuft noch
        wa_module._service_process = mock_proc

        from bot.whatsapp import stop_service

        await stop_service()

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
# whatsapp_agent
# ---------------------------------------------------------------------------


def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": 12345,
        "next_agent": None,
    }


async def _run_agent(text: str, thread_id: str) -> dict:
    """Phase 225: whatsapp_agent nutzt interrupt() – braucht Runnable-Kontext.
    Läuft den Agent über einen Single-Node-Graph bis zum Interrupt."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import StateGraph, START, END
    from agent.agents.whatsapp_agent import whatsapp_agent
    from agent.state import AgentState

    g = StateGraph(AgentState)
    g.add_node("agent", whatsapp_agent)
    g.add_edge(START, "agent")
    g.add_edge("agent", END)
    app = g.compile(checkpointer=MemorySaver())
    return await app.ainvoke(_state(text), {"configurable": {"thread_id": thread_id}})


_CONTACTS = [
    {"name": "Steffi", "whatsapp_name": "Steffi 🌞"},
    {"name": "Amalia", "whatsapp_name": "Amalia"},
    {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"},
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
        assert "jonas" in content.lower() and "nicht" in content.lower()
        assert "erlaubte kontakte" not in content.lower()

    async def test_valid_contact_returns_hitl(self):
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Ich komme später"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await _run_agent("Schick Steffi dass ich später komme", "wa-valid")
        value = result["__interrupt__"][0].value
        assert value["type"] == "whatsapp"
        assert value["whatsapp_name"] == "Steffi 🌞"
        assert "Ich komme später" in value["message"]

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
        llm_response = AIMessage(content='{"contact": "Steffi", "message": "Test"}')
        contact = {"name": "Steffi", "whatsapp_name": "Steffi 🌞"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await _run_agent("Schick Steffi Test", "wa-emoji")
        assert result["__interrupt__"][0].value["whatsapp_name"] == "Steffi 🌞"

    async def test_fabio_self_send(self):
        llm_response = AIMessage(content='{"contact": "Fabio", "message": "Test 123"}')
        contact = {"name": "Fabio", "whatsapp_name": "Fabio Morena (du)"}
        with (
            patch("bot.whatsapp.is_session_ready", return_value=True),
            patch("agent.agents.whatsapp_agent.get_llm") as mock_llm,
            patch("bot.whatsapp.find_contact", return_value=contact),
            patch("agent.agents.whatsapp_agent.log_action"),
        ):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=llm_response)
            result = await _run_agent("Schick mir selbst Test 123", "wa-self")
        value = result["__interrupt__"][0].value
        assert value["type"] == "whatsapp"
        assert value["whatsapp_name"] == "Fabio Morena (du)"
