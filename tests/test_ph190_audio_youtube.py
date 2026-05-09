"""
tests/test_ph190_audio_youtube.py – Phase 190 (#138 + #144)

Testet:
- on_audio: Transkription und Weiterleitung an handle_message_text
- on_document: Dispatch für audio/* MIME
- _handle_document_audio: Größencheck, Transkription, Fehlerfall
- youtube_agent: Transcript-API-Erfolg, Whisper-Fallback, ungültige URL
- supervisor: Pre-Routing für YouTube-URLs
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.auth import ALLOWED_IDS

_ALLOWED_ID = next(iter(ALLOWED_IDS))


def _make_thinking_mock():
    thinking = MagicMock()
    thinking.edit_text = AsyncMock()
    return thinking


def _make_update(mime_type=None, file_size=None, audio_file_id="audio_123"):
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.file_id = audio_file_id
    doc.file_name = "test.mp3"

    audio = MagicMock()
    audio.file_id = audio_file_id

    msg = MagicMock()
    msg.document = doc if mime_type else None
    msg.audio = audio
    msg.reply_text = AsyncMock(return_value=_make_thinking_mock())

    update = MagicMock()
    update.effective_chat.id = 42
    update.effective_user.id = _ALLOWED_ID
    update.message = msg
    return update


def _make_ctx(audio_bytes=b"fake_audio"):
    tg_file = AsyncMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(audio_bytes))
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=tg_file)
    ctx = MagicMock()
    ctx.bot = bot
    return ctx


# ---------------------------------------------------------------------------
# on_audio
# ---------------------------------------------------------------------------


class TestOnAudio:
    @pytest.mark.asyncio
    async def test_transcribes_and_routes(self):
        from bot.bot import on_audio

        update = _make_update()
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot.transcribe_audio", AsyncMock(return_value="Hallo Welt")) as mock_trans,
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
            patch("bot.bot._delete_thinking", AsyncMock()),
        ):
            await on_audio(update, ctx)

        mock_trans.assert_called_once()
        mock_hmt.assert_called_once()
        text_arg = mock_hmt.call_args[0][2]
        assert text_arg == "Hallo Welt"

    @pytest.mark.asyncio
    async def test_duplicate_skipped(self):
        from bot.bot import on_audio

        update = _make_update()
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=True)),
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
        ):
            await on_audio(update, ctx)

        mock_hmt.assert_not_called()

    @pytest.mark.asyncio
    async def test_transcription_failure_replies_error(self):
        from bot.bot import on_audio

        update = _make_update()
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot.transcribe_audio", AsyncMock(return_value=None)),
            patch("bot.bot._delete_thinking", AsyncMock()),
        ):
            await on_audio(update, ctx)

        update.message.reply_text.assert_called()
        last_call = update.message.reply_text.call_args_list[-1][0][0]
        assert "fehlgeschlagen" in last_call


# ---------------------------------------------------------------------------
# on_document – audio/* Dispatch
# ---------------------------------------------------------------------------


class TestOnDocumentAudioDispatch:
    @pytest.mark.asyncio
    async def test_audio_mime_calls_audio_handler(self):
        from bot.bot import on_document

        update = _make_update(mime_type="audio/mpeg", file_size=1000)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot._handle_document_audio", AsyncMock()) as mock_audio,
        ):
            await on_document(update, ctx)

        mock_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_ogg_mime_calls_audio_handler(self):
        from bot.bot import on_document

        update = _make_update(mime_type="audio/ogg", file_size=500)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot._handle_document_audio", AsyncMock()) as mock_audio,
        ):
            await on_document(update, ctx)

        mock_audio.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_mime_still_replies_info(self):
        from bot.bot import on_document

        update = _make_update(mime_type="application/zip", file_size=100)
        ctx = _make_ctx()
        with patch("bot.bot._is_duplicate", AsyncMock(return_value=False)):
            await on_document(update, ctx)

        update.message.reply_text.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_document_audio
# ---------------------------------------------------------------------------


class TestHandleDocumentAudio:
    @pytest.mark.asyncio
    async def test_too_large_replies_error(self):
        from bot.bot import _handle_document_audio, _AUDIO_MAX_BYTES

        update = _make_update(mime_type="audio/mpeg", file_size=_AUDIO_MAX_BYTES + 1)
        ctx = _make_ctx()
        await _handle_document_audio(update, ctx, update.message.document, 42)

        update.message.reply_text.assert_called_once()
        assert "zu groß" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_transcribes_and_passes_text(self):
        from bot.bot import _handle_document_audio

        update = _make_update(mime_type="audio/mpeg", file_size=1000)
        ctx = _make_ctx()
        with (
            patch("bot.bot.transcribe_audio", AsyncMock(return_value="Test Transkription")) as mock_trans,
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
            patch("bot.bot._delete_thinking", AsyncMock()),
        ):
            await _handle_document_audio(update, ctx, update.message.document, 42)

        mock_trans.assert_called_once()
        mock_hmt.assert_called_once()
        assert mock_hmt.call_args[0][2] == "Test Transkription"

    @pytest.mark.asyncio
    async def test_transcription_failure_replies_error(self):
        from bot.bot import _handle_document_audio

        update = _make_update(mime_type="audio/mpeg", file_size=1000)
        ctx = _make_ctx()
        with (
            patch("bot.bot.transcribe_audio", AsyncMock(return_value=None)),
            patch("bot.bot._delete_thinking", AsyncMock()),
        ):
            await _handle_document_audio(update, ctx, update.message.document, 42)

        calls = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("fehlgeschlagen" in c for c in calls)


# ---------------------------------------------------------------------------
# youtube_agent
# ---------------------------------------------------------------------------


def _make_state(text: str) -> dict:
    from langchain_core.messages import HumanMessage

    return {
        "messages": [HumanMessage(content=text)],
        "telegram_chat_id": 42,
        "next_agent": None,
        "last_agent_name": None,
        "image_data": None,
        "image_media_type": None,
    }


class TestYoutubeAgent:
    @pytest.mark.asyncio
    async def test_transcript_api_success(self):
        from agent.agents.youtube_agent import youtube_agent

        state = _make_state("Fass mir dieses Video zusammen: https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        mock_entries = [{"text": "Hallo", "start": 0.0}, {"text": "Welt", "start": 1.0}]
        mock_llm_response = MagicMock()
        mock_llm_response.content = "Eine Zusammenfassung."

        with (
            patch("agent.agents.youtube_agent._fetch_transcript", AsyncMock(return_value="Hallo Welt")) as mock_fetch,
            patch("agent.agents.youtube_agent.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value.ainvoke = AsyncMock(return_value=mock_llm_response)
            result = await youtube_agent(state)

        mock_fetch.assert_called_once_with("dQw4w9WgXcQ")
        from langchain_core.messages import AIMessage

        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert result["last_agent_name"] == "youtube_agent"

    @pytest.mark.asyncio
    async def test_whisper_fallback_used_when_no_transcript(self):
        from agent.agents.youtube_agent import youtube_agent

        state = _make_state("https://youtu.be/dQw4w9WgXcQ")
        mock_llm_response = MagicMock()
        mock_llm_response.content = "Whisper-Antwort."

        with (
            patch("agent.agents.youtube_agent._fetch_transcript", AsyncMock(return_value=None)),
            patch(
                "agent.agents.youtube_agent._whisper_fallback", AsyncMock(return_value="Transkript via Whisper")
            ) as mock_fallback,
            patch("agent.agents.youtube_agent.get_llm") as mock_get_llm,
        ):
            mock_get_llm.return_value.ainvoke = AsyncMock(return_value=mock_llm_response)
            result = await youtube_agent(state)

        mock_fallback.assert_called_once_with("dQw4w9WgXcQ")
        assert result["last_agent_name"] == "youtube_agent"

    @pytest.mark.asyncio
    async def test_no_transcript_at_all_replies_error(self):
        from agent.agents.youtube_agent import youtube_agent
        from langchain_core.messages import AIMessage

        state = _make_state("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        with (
            patch("agent.agents.youtube_agent._fetch_transcript", AsyncMock(return_value=None)),
            patch("agent.agents.youtube_agent._whisper_fallback", AsyncMock(return_value=None)),
        ):
            result = await youtube_agent(state)

        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert "kein Transkript" in last_msg.content

    @pytest.mark.asyncio
    async def test_invalid_url_replies_error(self):
        from agent.agents.youtube_agent import youtube_agent
        from langchain_core.messages import AIMessage

        state = _make_state("Schau mal diese Seite an: https://example.com/video")
        result = await youtube_agent(state)

        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert "keine gültige YouTube-URL" in last_msg.content.lower() or "keine" in last_msg.content.lower()

    @pytest.mark.asyncio
    async def test_youtu_be_short_url_extracted(self):
        from agent.agents.youtube_agent import _extract_video_id

        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtube_full_url_extracted(self):
        from agent.agents.youtube_agent import _extract_video_id

        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_no_url_returns_none(self):
        from agent.agents.youtube_agent import _extract_video_id

        assert _extract_video_id("Hallo, wie geht es dir?") is None


# ---------------------------------------------------------------------------
# supervisor – YouTube Pre-Routing
# ---------------------------------------------------------------------------


class TestSupervisorYoutubeRouting:
    def test_youtube_com_url_routes_to_youtube_agent(self):
        from langchain_core.messages import HumanMessage
        from agent.supervisor import _pre_route_youtube

        routing = [HumanMessage(content="https://www.youtube.com/watch?v=abc123")]
        result = _pre_route_youtube({}, [], routing)
        assert result == "youtube_agent"

    def test_youtu_be_url_routes_to_youtube_agent(self):
        from langchain_core.messages import HumanMessage
        from agent.supervisor import _pre_route_youtube

        routing = [HumanMessage(content="Schau mal: https://youtu.be/xyz789")]
        result = _pre_route_youtube({}, [], routing)
        assert result == "youtube_agent"

    def test_non_youtube_url_returns_none(self):
        from langchain_core.messages import HumanMessage
        from agent.supervisor import _pre_route_youtube

        routing = [HumanMessage(content="https://example.com/video")]
        result = _pre_route_youtube({}, [], routing)
        assert result is None

    def test_empty_routing_returns_none(self):
        from agent.supervisor import _pre_route_youtube

        result = _pre_route_youtube({}, [], [])
        assert result is None
