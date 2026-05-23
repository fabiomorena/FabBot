"""Tests für bot/local_api.py – Token, Auth, HTTP-Handler."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import bot.local_api as local_api_module


@pytest.fixture(autouse=True)
def reset_queue():
    local_api_module._message_queue = None
    yield
    local_api_module._message_queue = None


class TestGetOrCreateToken:
    def test_reads_existing_token(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("existierender-token")

        with patch("bot.local_api.TOKEN_PATH", token_file):
            result = local_api_module.get_or_create_token()

        assert result == "existierender-token"

    def test_creates_new_token_when_missing(self, tmp_path):
        token_file = tmp_path / ".fabbot" / "local_api_token"

        with patch("bot.local_api.TOKEN_PATH", token_file):
            result = local_api_module.get_or_create_token()

        assert len(result) > 20
        assert token_file.read_text().strip() == result

    def test_new_token_file_has_restricted_permissions(self, tmp_path):
        token_file = tmp_path / ".fabbot" / "local_api_token"

        with patch("bot.local_api.TOKEN_PATH", token_file):
            local_api_module.get_or_create_token()

        assert oct(token_file.stat().st_mode)[-3:] == "600"


class TestGetQueue:
    def test_returns_queue_instance(self):
        q = local_api_module.get_queue()
        assert isinstance(q, asyncio.Queue)

    def test_returns_same_instance(self):
        q1 = local_api_module.get_queue()
        q2 = local_api_module.get_queue()
        assert q1 is q2


class TestCheckAuth:
    def test_valid_token_returns_true(self):
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {local_api_module.LOCAL_API_TOKEN}"
        assert local_api_module._check_auth(request) is True

    def test_wrong_token_returns_false(self):
        request = MagicMock()
        request.headers.get.return_value = "Bearer falsch"
        assert local_api_module._check_auth(request) is False

    def test_missing_header_returns_false(self):
        request = MagicMock()
        request.headers.get.return_value = ""
        assert local_api_module._check_auth(request) is False


class TestHandleMessage:
    def _authed_request(self, body: dict) -> MagicMock:
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {local_api_module.LOCAL_API_TOKEN}"
        request.json = AsyncMock(return_value=body)
        request.remote = "127.0.0.1"
        return request

    async def test_valid_message_queued(self):
        request = self._authed_request({"text": "Hallo Bot"})
        response = await local_api_module._handle_message(request)
        assert response.status == 200
        assert not local_api_module.get_queue().empty()

    async def test_unauthorized_returns_401(self):
        request = MagicMock()
        request.headers.get.return_value = "Bearer falsch"
        request.remote = "127.0.0.1"
        response = await local_api_module._handle_message(request)
        assert response.status == 401

    async def test_empty_text_returns_400(self):
        request = self._authed_request({"text": "   "})
        response = await local_api_module._handle_message(request)
        assert response.status == 400

    async def test_missing_text_key_returns_400(self):
        request = self._authed_request({})
        response = await local_api_module._handle_message(request)
        assert response.status == 400

    async def test_json_error_returns_500(self):
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {local_api_module.LOCAL_API_TOKEN}"
        request.json = AsyncMock(side_effect=ValueError("invalid json"))
        request.remote = "127.0.0.1"
        response = await local_api_module._handle_message(request)
        assert response.status == 500


class TestHandleStatus:
    async def test_authorized_returns_running(self):
        request = MagicMock()
        request.headers.get.return_value = f"Bearer {local_api_module.LOCAL_API_TOKEN}"
        response = await local_api_module._handle_status(request)
        assert response.status == 200

    async def test_unauthorized_returns_401(self):
        request = MagicMock()
        request.headers.get.return_value = "Bearer falsch"
        response = await local_api_module._handle_status(request)
        assert response.status == 401
