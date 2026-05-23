"""
tests/test_ph189_attachments.py – Phase 189 (#137 + #136)

Testet:
- on_document: Dispatch auf Image / PDF / unbekannter Typ
- _handle_document_pdf: Text-Extraktion, Größen-Limit, leerer Text, Kürzen
- on_location: Koordinaten werden als [Standort]-Text an handle_message_text übergeben
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.auth import ALLOWED_IDS

_ALLOWED_ID = next(iter(ALLOWED_IDS))


def _make_update(mime_type=None, file_size=None, caption=None, location=None, filename=None):
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.file_size = file_size
    doc.file_id = "file_123"
    doc.file_name = filename or "test.pdf"

    msg = MagicMock()
    msg.document = doc if mime_type else None
    msg.caption = caption
    msg.location = location
    msg.reply_text = AsyncMock(return_value=MagicMock())

    update = MagicMock()
    update.effective_chat.id = 42
    update.effective_user.id = _ALLOWED_ID
    update.message = msg
    return update


def _make_ctx(pdf_bytes=b""):
    tg_file = AsyncMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(pdf_bytes))
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=tg_file)
    ctx = MagicMock()
    ctx.bot = bot
    return ctx


# ---------------------------------------------------------------------------
# on_document – Dispatch
# ---------------------------------------------------------------------------


class TestOnDocumentDispatch:
    @pytest.mark.asyncio
    async def test_no_doc_replies_error(self):
        from bot.bot import on_document

        update = _make_update()
        update.message.document = None
        ctx = _make_ctx()
        with patch("bot.bot._is_duplicate", AsyncMock(return_value=False)):
            await on_document(update, ctx)
        update.message.reply_text.assert_called_once()
        assert "verarbeitet" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unsupported_mime_replies_info(self):
        from bot.bot import on_document

        update = _make_update(mime_type="application/zip", file_size=100)
        ctx = _make_ctx()
        with patch("bot.bot._is_duplicate", AsyncMock(return_value=False)):
            await on_document(update, ctx)
        update.message.reply_text.assert_called_once()
        assert "application/zip" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_image_mime_calls_image_handler(self):
        from bot.bot import on_document

        update = _make_update(mime_type="image/jpeg", file_size=100)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot._handle_document_image", AsyncMock()) as mock_img,
        ):
            await on_document(update, ctx)
        mock_img.assert_called_once()

    @pytest.mark.asyncio
    async def test_pdf_mime_calls_pdf_handler(self):
        from bot.bot import on_document

        update = _make_update(mime_type="application/pdf", file_size=100)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot._handle_document_pdf", AsyncMock()) as mock_pdf,
        ):
            await on_document(update, ctx)
        mock_pdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_duplicate_returns_early(self):
        from bot.bot import on_document

        update = _make_update(mime_type="application/pdf", file_size=100)
        ctx = _make_ctx()
        with patch("bot.bot._is_duplicate", AsyncMock(return_value=True)):
            await on_document(update, ctx)
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_document_pdf
# ---------------------------------------------------------------------------


def _make_fitz_doc(pages_text: list[str]):
    pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text = MagicMock(return_value=text)
        pages.append(page)
    fitz_doc = MagicMock()
    fitz_doc.__enter__ = MagicMock(return_value=fitz_doc)
    fitz_doc.__exit__ = MagicMock(return_value=False)
    fitz_doc.__iter__ = MagicMock(return_value=iter(pages))
    return fitz_doc


class TestHandleDocumentPdf:
    @pytest.mark.asyncio
    async def test_pdf_too_large_replies_error(self):
        from bot.bot import _handle_document_pdf

        update = _make_update(mime_type="application/pdf", file_size=25_000_000)
        ctx = _make_ctx()
        await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        update.message.reply_text.assert_called_once()
        assert "zu groß" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_empty_pdf_replies_no_text(self):
        from bot.bot import _handle_document_pdf

        update = _make_update(mime_type="application/pdf", file_size=1000)
        ctx = _make_ctx(pdf_bytes=b"%PDF-1.4")
        fitz_doc = _make_fitz_doc([""])
        with (
            patch("bot.bot._delete_thinking", AsyncMock()),
            patch("fitz.open", MagicMock(return_value=fitz_doc)),
        ):
            await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        update.message.reply_text.assert_called()
        combined = " ".join(c[0][0] for c in update.message.reply_text.call_args_list)
        assert "keinen lesbaren Text" in combined

    @pytest.mark.asyncio
    async def test_pdf_text_passed_to_invoke(self):
        from bot.bot import _handle_document_pdf

        update = _make_update(mime_type="application/pdf", file_size=1000, filename="bericht.pdf")
        ctx = _make_ctx(pdf_bytes=b"%PDF")
        fitz_doc = _make_fitz_doc(["Seite 1 Inhalt", "Seite 2 Inhalt"])
        with (
            patch("bot.bot._delete_thinking", AsyncMock()),
            patch("fitz.open", MagicMock(return_value=fitz_doc)),
            patch("bot.bot._invoke_and_extract", AsyncMock(return_value="")) as mock_inv,
            patch("bot.bot._dispatch_response", AsyncMock()),
            patch("bot.bot._get_invoke_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)
            await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        mock_inv.assert_called_once()
        state_arg = mock_inv.call_args[0][0]
        text_arg = state_arg["messages"][0].content
        assert "[PDF: bericht.pdf]" in text_arg
        assert "Seite 1 Inhalt" in text_arg
        assert "Seite 2 Inhalt" in text_arg

    @pytest.mark.asyncio
    async def test_pdf_caption_included_in_message(self):
        from bot.bot import _handle_document_pdf

        update = _make_update(mime_type="application/pdf", file_size=1000, caption="Bitte zusammenfassen")
        ctx = _make_ctx(pdf_bytes=b"%PDF")
        fitz_doc = _make_fitz_doc(["Inhalt"])
        with (
            patch("bot.bot._delete_thinking", AsyncMock()),
            patch("fitz.open", MagicMock(return_value=fitz_doc)),
            patch("bot.bot._invoke_and_extract", AsyncMock(return_value="")) as mock_inv,
            patch("bot.bot._dispatch_response", AsyncMock()),
            patch("bot.bot._get_invoke_lock") as mock_lock,
            patch("bot.bot.sanitize_input_async", AsyncMock(return_value=(True, "Bitte zusammenfassen"))),
        ):
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)
            await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        text_arg = mock_inv.call_args[0][0]["messages"][0].content
        assert "Bitte zusammenfassen" in text_arg

    @pytest.mark.asyncio
    async def test_pdf_text_truncated_at_limit(self):
        from bot.bot import _handle_document_pdf
        from agent.config import get_settings

        long_text = "X" * (get_settings().pdf_max_chars + 5000)
        update = _make_update(mime_type="application/pdf", file_size=1000)
        ctx = _make_ctx(pdf_bytes=b"%PDF")
        fitz_doc = _make_fitz_doc([long_text])
        with (
            patch("bot.bot._delete_thinking", AsyncMock()),
            patch("fitz.open", MagicMock(return_value=fitz_doc)),
            patch("bot.bot._invoke_and_extract", AsyncMock(return_value="")) as mock_inv,
            patch("bot.bot._dispatch_response", AsyncMock()),
            patch("bot.bot._get_invoke_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)
            await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        text_arg = mock_inv.call_args[0][0]["messages"][0].content
        assert "gekürzt" in text_arg

    @pytest.mark.asyncio
    async def test_blocked_caption_aborts(self):
        from bot.bot import _handle_document_pdf

        update = _make_update(mime_type="application/pdf", file_size=1000, caption="<script>evil</script>")
        ctx = _make_ctx()
        with (
            patch("bot.bot._delete_thinking", AsyncMock()),
            patch("bot.bot.sanitize_input_async", AsyncMock(return_value=(False, "XSS erkannt"))),
            patch("bot.bot.log_blocked", MagicMock()),
        ):
            await _handle_document_pdf(update, ctx, update.message.document, 42, 99)
        update.message.reply_text.assert_called_once()
        assert "abgelehnt" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# on_location
# ---------------------------------------------------------------------------


class TestOnLocation:
    @pytest.mark.asyncio
    async def test_location_passed_to_handle_message_text(self):
        from bot.bot import on_location

        loc = MagicMock()
        loc.latitude = 52.5200
        loc.longitude = 13.4050
        update = _make_update(location=loc)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
        ):
            await on_location(update, ctx)
        mock_hmt.assert_called_once()
        text_arg = mock_hmt.call_args[0][2]
        assert "[Standort]" in text_arg
        assert "52.52" in text_arg
        assert "13.405" in text_arg

    @pytest.mark.asyncio
    async def test_location_duplicate_skipped(self):
        from bot.bot import on_location

        loc = MagicMock()
        loc.latitude = 52.0
        loc.longitude = 13.0
        update = _make_update(location=loc)
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=True)),
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
        ):
            await on_location(update, ctx)
        mock_hmt.assert_not_called()

    @pytest.mark.asyncio
    async def test_location_none_returns_early(self):
        from bot.bot import on_location

        update = _make_update()
        update.message.location = None
        ctx = _make_ctx()
        with (
            patch("bot.bot._is_duplicate", AsyncMock(return_value=False)),
            patch("bot.bot.handle_message_text", AsyncMock()) as mock_hmt,
        ):
            await on_location(update, ctx)
        mock_hmt.assert_not_called()
