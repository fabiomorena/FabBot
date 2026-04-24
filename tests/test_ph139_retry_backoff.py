"""
tests/test_ph139_retry_backoff.py – Phase 139 (Issue #63)

Testet Retry-Backoff für transiente Fehler in _invoke_with_retry:
- APIConnectionError → retry mit Backoff
- RateLimitError → retry mit Backoff
- Nicht-transiente Fehler (400, GraphRecursionError) → kein Retry
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from anthropic import APIConnectionError, RateLimitError, APIStatusError


def _make_connection_error() -> APIConnectionError:
    request = MagicMock()
    return APIConnectionError(request=request)


def _make_rate_limit_error() -> RateLimitError:
    response = MagicMock()
    response.status_code = 429
    return RateLimitError("rate limited", response=response, body={})


def _make_api_error(status_code: int) -> APIStatusError:
    response = MagicMock()
    response.status_code = status_code
    return APIStatusError(f"error {status_code}", response=response, body={})


class TestRetryOnConnectionError:
    @classmethod
    def setup_class(cls):
        from bot.bot import _invoke_with_retry
        cls._invoke_with_retry = staticmethod(_invoke_with_retry)

    async def test_retries_on_connection_error_then_success(self):
        expected = {"messages": []}
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [_make_connection_error(), expected]

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await self._invoke_with_retry({}, {})

        assert result == expected
        assert mock_graph.ainvoke.call_count == 2

    async def test_all_attempts_connection_error_raises(self):
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            _make_connection_error(),
            _make_connection_error(),
            _make_connection_error(),
        ]

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(APIConnectionError):
                await self._invoke_with_retry({}, {})

        assert mock_graph.ainvoke.call_count == 3

    async def test_connection_error_backoff_delays(self):
        expected = {"messages": []}
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            _make_connection_error(),
            _make_connection_error(),
            expected,
        ]
        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", side_effect=mock_sleep):
            await self._invoke_with_retry({}, {})

        assert sleep_calls == [2.0, 4.0]


class TestRetryOnRateLimitError:
    @classmethod
    def setup_class(cls):
        from bot.bot import _invoke_with_retry
        cls._invoke_with_retry = staticmethod(_invoke_with_retry)

    async def test_retries_on_rate_limit_then_success(self):
        expected = {"messages": []}
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [_make_rate_limit_error(), expected]

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await self._invoke_with_retry({}, {})

        assert result == expected
        assert mock_graph.ainvoke.call_count == 2

    async def test_all_attempts_rate_limit_raises(self):
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            _make_rate_limit_error(),
            _make_rate_limit_error(),
            _make_rate_limit_error(),
        ]

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RateLimitError):
                await self._invoke_with_retry({}, {})

        assert mock_graph.ainvoke.call_count == 3


class TestNoRetryOnFatalErrors:
    @classmethod
    def setup_class(cls):
        from bot.bot import _invoke_with_retry
        cls._invoke_with_retry = staticmethod(_invoke_with_retry)

    async def test_400_not_retried(self):
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = _make_api_error(400)

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(APIStatusError) as exc_info:
                await self._invoke_with_retry({}, {})

        assert exc_info.value.status_code == 400
        assert mock_graph.ainvoke.call_count == 1

    async def test_graph_recursion_error_not_retried(self):
        from langgraph.errors import GraphRecursionError
        mock_graph = AsyncMock()
        mock_graph.ainvoke.side_effect = GraphRecursionError("too deep")

        with patch("agent.supervisor.agent_graph", mock_graph), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GraphRecursionError):
                await self._invoke_with_retry({}, {})

        assert mock_graph.ainvoke.call_count == 1
