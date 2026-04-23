"""
tests/test_ph133_web_exceptions.py – Issue #30
Spezifischere Exception-Handler in web_agent(): DNS vs. 404 vs. Timeout.
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage


def _make_state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": None,
    }


def _make_llm_mock(action: str = "fetch", url: str = "https://example.com") -> AsyncMock:
    """LLM-Mock der fetch-Action mit gegebener URL zurückgibt."""
    mock = AsyncMock()
    import json
    mock.ainvoke.return_value = MagicMock(
        content=json.dumps({"action": action, "url": url, "query": "", "engine": "auto"})
    )
    return mock


@pytest.mark.asyncio
async def test_dns_error_returns_host_nicht_erreichbar():
    """ConnectError (DNS) → 'Host nicht erreichbar.'"""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://nonexistent.invalid")
    llm_mock = _make_llm_mock(action="fetch", url="https://nonexistent.invalid")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("getaddrinfo failed")
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    msg = result["messages"][-1].content
    assert msg == "Host nicht erreichbar."


@pytest.mark.asyncio
async def test_http_404_returns_seite_nicht_gefunden():
    """HTTPStatusError 404 → 'Seite nicht gefunden.'"""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://example.com/missing")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com/missing")

    request = httpx.Request("GET", "https://example.com/missing")
    response_404 = httpx.Response(404, request=request)
    error_404 = httpx.HTTPStatusError("404", request=request, response=response_404)

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = error_404
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    msg = result["messages"][-1].content
    assert msg == "Seite nicht gefunden."


@pytest.mark.asyncio
async def test_http_500_returns_generic_http_error():
    """HTTPStatusError 500 → 'HTTP Fehler: 500' (bestehende Nachricht bleibt)."""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://example.com/error")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com/error")

    request = httpx.Request("GET", "https://example.com/error")
    response_500 = httpx.Response(500, request=request)
    error_500 = httpx.HTTPStatusError("500", request=request, response=response_500)

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = error_500
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    msg = result["messages"][-1].content
    assert msg == "HTTP Fehler: 500"


@pytest.mark.asyncio
async def test_timeout_returns_timeout_message():
    """TimeoutException → 'Timeout beim Abrufen der Webseite.' (bestehend)."""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://example.com/slow")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com/slow")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    msg = result["messages"][-1].content
    assert msg == "Timeout beim Abrufen der Webseite."


@pytest.mark.asyncio
async def test_last_agent_name_set_on_error():
    """last_agent_name ist immer 'web_agent', auch bei Fehlern."""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://nonexistent.invalid")
    llm_mock = _make_llm_mock(action="fetch", url="https://nonexistent.invalid")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.ConnectError("DNS fail")
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    assert result["last_agent_name"] == "web_agent"
    assert result["last_agent_result"] == "Host nicht erreichbar."


def _make_status_error(code: int, url: str = "https://example.com") -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(str(code), request=req, response=resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    (403, "Zugriff verweigert."),
    (429, "Zu viele Anfragen – bitte später erneut versuchen."),
    (503, "Server momentan nicht verfügbar."),
])
async def test_specific_http_status_messages(code, expected):
    """403/429/503 geben nutzerfreundliche Meldungen."""
    from agent.agents.web import web_agent

    state = _make_state(f"Öffne https://example.com/test")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com/test")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = _make_status_error(code)
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    assert result["messages"][-1].content == expected


@pytest.mark.asyncio
async def test_transport_error_returns_netzwerkfehler():
    """TransportError (z.B. ReadError) → 'Netzwerkfehler beim Abrufen der Webseite.'"""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://example.com/broken")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com/broken")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.ReadError("connection reset")
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    assert result["messages"][-1].content == "Netzwerkfehler beim Abrufen der Webseite."


@pytest.mark.asyncio
async def test_generic_exception_does_not_leak_details():
    """Unbekannte Exception gibt keine internen Details an den User weiter."""
    from agent.agents.web import web_agent

    state = _make_state("Öffne https://example.com")
    llm_mock = _make_llm_mock(action="fetch", url="https://example.com")

    with patch("agent.agents.web.get_llm", return_value=llm_mock), \
         patch("agent.agents.web._is_ssrf_blocked", return_value=(False, "")), \
         patch("agent.agents.web.log_action"), \
         patch("httpx.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = RuntimeError("internal secret path: /etc/passwd")
        mock_client_cls.return_value = mock_client

        result = await web_agent(state)

    msg = result["messages"][-1].content
    assert msg == "Fehler beim Abrufen der Webseite."
    assert "secret" not in msg
    assert "/etc" not in msg
