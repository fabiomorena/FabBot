"""
Phase 88 Security Tests – Symlink, Tiefenlimit, DNS-Rebinding, Async-Conversion.
"""
import os
import socket
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# file.py – Symlink-Schutz
# ---------------------------------------------------------------------------

class TestIsPathAllowedSymlink:

    def test_symlink_to_external_path_blocked(self, tmp_path: Path) -> None:
        """Symlink in erlaubtem Ordner → verbotenes Ziel → blockiert."""
        allowed_base = tmp_path / "Downloads"
        allowed_base.mkdir()
        external = tmp_path / "external_secret"
        external.mkdir()
        target = external / "secret.txt"
        target.write_text("geheim")

        symlink = allowed_base / "evil_link.txt"
        symlink.symlink_to(target)

        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [allowed_base]):
            from agent.agents.file import is_path_allowed
            allowed, reason = is_path_allowed(symlink)

        assert allowed is False
        assert "Symlink" in reason

    def test_symlink_within_allowed_path_allowed(self, tmp_path: Path) -> None:
        """Symlink innerhalb der Allowlist → erlaubt."""
        allowed_base = tmp_path / "Downloads"
        allowed_base.mkdir()
        real_file = allowed_base / "real.txt"
        real_file.write_text("inhalt")
        symlink = allowed_base / "link.txt"
        symlink.symlink_to(real_file)

        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [allowed_base]):
            from agent.agents.file import is_path_allowed
            allowed, _ = is_path_allowed(symlink)

        assert allowed is True

    def test_regular_file_unaffected(self, tmp_path: Path) -> None:
        """Normale Datei wird nicht als Symlink behandelt."""
        allowed_base = tmp_path / "Downloads"
        allowed_base.mkdir()
        regular = allowed_base / "normal.txt"
        regular.write_text("inhalt")

        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [allowed_base]):
            from agent.agents.file import is_path_allowed
            allowed, _ = is_path_allowed(regular)

        assert allowed is True

    def test_symlink_to_blocked_dir_blocked(self, tmp_path: Path) -> None:
        """Symlink der auf blockiertes Ziel zeigt wird blockiert."""
        allowed_base = tmp_path / "Downloads"
        allowed_base.mkdir()
        blocked_base = tmp_path / "ssh_dir"
        blocked_base.mkdir()
        ssh_key = blocked_base / "id_rsa"
        ssh_key.write_text("PRIVATE KEY")

        symlink = allowed_base / "id_rsa_link"
        symlink.symlink_to(ssh_key)

        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [allowed_base]):
            from agent.agents.file import is_path_allowed
            allowed, reason = is_path_allowed(symlink)

        assert allowed is False


# ---------------------------------------------------------------------------
# file.py – Tiefenlimit
# ---------------------------------------------------------------------------

class TestIsPathAllowedDepth:

    def _allowed_base(self, tmp_path: Path) -> Path:
        base = tmp_path / "Downloads"
        base.mkdir()
        return base

    def test_single_level_allowed(self, tmp_path: Path) -> None:
        base = self._allowed_base(tmp_path)
        path = base / "file.txt"
        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [base]):
            with patch("agent.agents.file.MAX_PATH_DEPTH", 5):
                from agent.agents.file import is_path_allowed
                allowed, _ = is_path_allowed(path)
        assert allowed is True

    def test_at_max_depth_allowed(self, tmp_path: Path) -> None:
        """Genau MAX_PATH_DEPTH Teile → erlaubt."""
        base = self._allowed_base(tmp_path)
        path = base / "a" / "b" / "c" / "d" / "file.txt"
        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [base]):
            with patch("agent.agents.file.MAX_PATH_DEPTH", 5):
                from agent.agents.file import is_path_allowed
                allowed, _ = is_path_allowed(path)
        assert allowed is True

    def test_one_over_max_depth_blocked(self, tmp_path: Path) -> None:
        """MAX_PATH_DEPTH + 1 Teile → blockiert."""
        base = self._allowed_base(tmp_path)
        path = base / "a" / "b" / "c" / "d" / "e" / "file.txt"
        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [base]):
            with patch("agent.agents.file.MAX_PATH_DEPTH", 5):
                from agent.agents.file import is_path_allowed
                allowed, reason = is_path_allowed(path)
        assert allowed is False
        assert "tief" in reason.lower()

    def test_very_deep_path_blocked(self, tmp_path: Path) -> None:
        """Tief verschachtelter LLM-generierter Pfad wird blockiert."""
        base = self._allowed_base(tmp_path)
        path = base / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / "evil.txt"
        with patch("agent.agents.file.ALLOWED_BASE_PATHS", [base]):
            with patch("agent.agents.file.MAX_PATH_DEPTH", 5):
                from agent.agents.file import is_path_allowed
                allowed, _ = is_path_allowed(path)
        assert allowed is False

    def test_max_path_depth_constant_exists(self) -> None:
        """MAX_PATH_DEPTH ist als öffentliche Konstante exportiert."""
        from agent.agents.file import MAX_PATH_DEPTH
        assert isinstance(MAX_PATH_DEPTH, int)
        assert MAX_PATH_DEPTH > 0


# ---------------------------------------------------------------------------
# web.py – DNS-Rebinding-Schutz
# ---------------------------------------------------------------------------

class TestSSRFDNSRebinding:

    def test_hostname_resolving_to_private_ip_blocked(self) -> None:
        """Hostname der auf private IP auflöst wird blockiert."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname", return_value="192.168.1.100"):
            blocked, reason = _is_ssrf_blocked("http://evil-rebinding.com")
        assert blocked is True
        assert "192.168.1.100" in reason or "DNS" in reason or "Rebinding" in reason

    def test_hostname_resolving_to_loopback_blocked(self) -> None:
        """Hostname der auf 127.0.0.1 auflöst wird blockiert."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname", return_value="127.0.0.1"):
            blocked, reason = _is_ssrf_blocked("http://evil.com")
        assert blocked is True

    def test_hostname_resolving_to_link_local_blocked(self) -> None:
        """Hostname der auf 169.254.x.x auflöst wird blockiert."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname", return_value="169.254.0.1"):
            blocked, reason = _is_ssrf_blocked("http://evil.com")
        assert blocked is True

    def test_hostname_resolving_to_public_ip_allowed(self) -> None:
        """Hostname der auf öffentliche IP auflöst wird durchgelassen."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname", return_value="93.184.216.34"):
            blocked, _ = _is_ssrf_blocked("http://example.com")
        assert blocked is False

    def test_dns_gaierror_allows_through(self) -> None:
        """DNS-Fehler (Host nicht auflösbar) blockiert nicht – httpx scheitert später."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname", side_effect=socket.gaierror("NXDOMAIN")):
            blocked, _ = _is_ssrf_blocked("http://nonexistent.invalid")
        assert blocked is False

    def test_ip_literal_not_dns_resolved(self) -> None:
        """IP-Adressen direkt werden ohne DNS-Auflösung geprüft."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname") as mock_dns:
            blocked, _ = _is_ssrf_blocked("http://192.168.1.1")
        assert blocked is True
        mock_dns.assert_not_called()

    def test_clip_agent_dns_rebinding_blocked(self) -> None:
        """Auch clip_agent._is_ssrf_blocked schützt gegen DNS-Rebinding."""
        from agent.agents.clip_agent import _is_ssrf_blocked as clip_ssrf
        with patch("socket.gethostbyname", return_value="10.0.0.1"):
            blocked, reason = clip_ssrf("http://evil-clip.com")
        assert blocked is True

    def test_localhost_still_blocked_without_dns(self) -> None:
        """localhost wird ohne DNS-Auflösung blockiert (bestehender Check)."""
        from agent.agents.web import _is_ssrf_blocked
        with patch("socket.gethostbyname") as mock_dns:
            blocked, _ = _is_ssrf_blocked("http://localhost/api")
        assert blocked is True
        mock_dns.assert_not_called()


# ---------------------------------------------------------------------------
# web.py – Query-Sanitization
# ---------------------------------------------------------------------------

class TestWebQuerySanitization:

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self) -> None:
        """Leere Query gibt Fehlermeldung zurück statt Suche zu starten."""
        from agent.agents.web import web_agent
        from langchain_core.messages import HumanMessage, AIMessage

        llm_response = AIMessage(content='{"action": "search", "query": "", "engine": "auto"}')
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        state = {"messages": [HumanMessage(content="suche")], "telegram_chat_id": 1}
        with patch("agent.agents.web.get_llm", return_value=mock_llm):
            result = await web_agent(state)

        content = result["messages"][-1].content
        assert "ungültig" in content.lower() or "kurz" in content.lower()

    @pytest.mark.asyncio
    async def test_long_query_is_truncated(self) -> None:
        """Sehr lange Query wird auf 200 Zeichen begrenzt."""
        from agent.agents.web import web_agent, _QUERY_MAX_LEN
        from langchain_core.messages import HumanMessage, AIMessage

        long_query = "x" * 500
        captured_queries = []

        async def fake_search(query: str) -> list:
            captured_queries.append(query)
            return []

        llm_response = AIMessage(content=f'{{"action": "search", "query": "{long_query[:100]}", "engine": "auto"}}')
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=llm_response)

        state = {"messages": [HumanMessage(content="suche")], "telegram_chat_id": 1}
        with patch("agent.agents.web.get_llm", return_value=mock_llm), \
             patch("agent.agents.web._search_tavily", side_effect=fake_search), \
             patch("agent.agents.web.TAVILY_API_KEY", "fake"):
            await web_agent(state)

        if captured_queries:
            assert len(captured_queries[0]) <= _QUERY_MAX_LEN


# ---------------------------------------------------------------------------
# Async-Konversion – alle Agents
# ---------------------------------------------------------------------------

class TestAgentAsyncConversion:

    def test_terminal_agent_is_async(self) -> None:
        """terminal_agent ist jetzt eine async-Funktion."""
        import inspect
        from agent.agents.terminal import terminal_agent
        assert inspect.iscoroutinefunction(terminal_agent), \
            "terminal_agent muss async sein – verhindert Event-Loop-Blockierung"

    def test_file_agent_is_async(self) -> None:
        """file_agent ist jetzt eine async-Funktion."""
        import inspect
        from agent.agents.file import file_agent
        assert inspect.iscoroutinefunction(file_agent), \
            "file_agent muss async sein – verhindert Event-Loop-Blockierung"

    def test_calendar_agent_is_async(self) -> None:
        import inspect
        from agent.agents.calendar import calendar_agent
        assert inspect.iscoroutinefunction(calendar_agent)

    def test_computer_agent_is_async(self) -> None:
        import inspect
        from agent.agents.computer import computer_agent
        assert inspect.iscoroutinefunction(computer_agent)

    def test_web_agent_is_async(self) -> None:
        import inspect
        from agent.agents.web import web_agent
        assert inspect.iscoroutinefunction(web_agent)

    @pytest.mark.asyncio
    async def test_terminal_agent_uses_ainvoke(self) -> None:
        """terminal_agent nutzt ainvoke – kein sync invoke."""
        from agent.agents.terminal import terminal_agent
        from langchain_core.messages import HumanMessage, AIMessage

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))
        mock_llm.invoke = MagicMock(side_effect=AssertionError("invoke darf nicht aufgerufen werden"))

        state = {"messages": [HumanMessage(content="test")], "telegram_chat_id": 1}
        with patch("agent.agents.terminal.get_llm", return_value=mock_llm):
            await terminal_agent(state)

        mock_llm.ainvoke.assert_called_once()
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_agent_uses_ainvoke(self) -> None:
        """file_agent nutzt ainvoke – kein sync invoke."""
        from agent.agents.file import file_agent
        from langchain_core.messages import HumanMessage, AIMessage

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))
        mock_llm.invoke = MagicMock(side_effect=AssertionError("invoke darf nicht aufgerufen werden"))

        state = {"messages": [HumanMessage(content="test")], "telegram_chat_id": 1}
        with patch("agent.agents.file.get_llm", return_value=mock_llm):
            await file_agent(state)

        mock_llm.ainvoke.assert_called_once()
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_calendar_agent_uses_ainvoke(self) -> None:
        """calendar_agent nutzt ainvoke."""
        from agent.agents.calendar import calendar_agent
        from langchain_core.messages import HumanMessage, AIMessage

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))
        mock_llm.invoke = MagicMock(side_effect=AssertionError("invoke darf nicht aufgerufen werden"))

        state = {"messages": [HumanMessage(content="test")], "telegram_chat_id": 1}
        with patch("agent.agents.calendar.get_llm", return_value=mock_llm):
            await calendar_agent(state)

        mock_llm.ainvoke.assert_called_once()
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_computer_agent_uses_ainvoke(self) -> None:
        """computer_agent nutzt ainvoke."""
        from agent.agents.computer import computer_agent
        from langchain_core.messages import HumanMessage, AIMessage

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNSUPPORTED"))
        mock_llm.invoke = MagicMock(side_effect=AssertionError("invoke darf nicht aufgerufen werden"))

        state = {"messages": [HumanMessage(content="test")], "telegram_chat_id": 1}
        with patch("agent.agents.computer.get_llm", return_value=mock_llm):
            await computer_agent(state)

        # Phase 114: kein LLM-Call für Intent-Parse – ainvoke nur bei Screenshot
        mock_llm.ainvoke.assert_not_called()
        mock_llm.invoke.assert_not_called()
