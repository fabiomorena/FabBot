"""
tests/test_ph232_markdown_fallback.py – Phase 232 (Issue #326)

Am 16.08.2026 kam kein Morning Briefing. Es war fertig generiert (13,5 s
Arbeit), scheiterte aber am Versand:

    Can't parse entities: can't find end of the entity starting at byte offset 360

Auslöser war ein Pending Item namens "youtube_agent" – der einzelne Unterstrich
startet für Telegrams Legacy-Markdown eine Kursiv-Entity, die nie endet, worauf
die *gesamte* Nachricht abgelehnt wird.

Der Briefing-Text besteht fast nur aus Fremdtext (RSS-Titel, Kalendertermine,
Pending Items, Erinnerungen im Wortlaut des Users). Ein einzelnes `_` oder `*`
darf keine Nachricht mehr verschlucken – bei den proaktiven Nachrichten merkt
es sonst niemand.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from bot.telegram_markdown import mit_markdown_fallback


def _parse_fehler():
    return BadRequest("Can't parse entities: can't find end of the entity starting at byte offset 360")


# ---------------------------------------------------------------------------
# Kern: Fallback auf Klartext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_erfolgsfall_sendet_mit_markdown():
    senden = AsyncMock(return_value="ok")

    ergebnis = await mit_markdown_fallback(senden, "*fett*", chat_id=7)

    assert ergebnis == "ok"
    senden.assert_awaited_once_with(text="*fett*", parse_mode="Markdown", chat_id=7)


@pytest.mark.asyncio
async def test_parse_fehler_sendet_ohne_parse_mode_erneut():
    """Der Fall vom 16.08.: Inhalt muss ankommen, notfalls unformatiert."""
    senden = AsyncMock(side_effect=[_parse_fehler(), "ok"])

    ergebnis = await mit_markdown_fallback(senden, "✅ youtube_agent", chat_id=7)

    assert ergebnis == "ok"
    assert senden.await_count == 2
    zweiter = senden.await_args_list[1].kwargs
    assert "parse_mode" not in zweiter
    assert zweiter["text"] == "✅ youtube_agent"
    assert zweiter["chat_id"] == 7


@pytest.mark.asyncio
async def test_original_text_bleibt_unveraendert():
    """Kein Escaping, kein Kürzen – der Nutzer soll lesen, was gemeint war."""
    text = "⏰ *Erinnerung:* Steuer_2026 abgeben * wichtig"
    senden = AsyncMock(side_effect=[_parse_fehler(), "ok"])

    await mit_markdown_fallback(senden, text, chat_id=1)

    assert senden.await_args_list[1].kwargs["text"] == text


@pytest.mark.asyncio
async def test_parse_fehler_wird_geloggt(caplog):
    """Sonst bleibt unsichtbar, wie oft der Fallback greift."""
    senden = AsyncMock(side_effect=[_parse_fehler(), "ok"])

    with caplog.at_level("WARNING", logger="bot.telegram_markdown"):
        await mit_markdown_fallback(senden, "a_b", chat_id=1)

    assert "Markdown" in caplog.text


# ---------------------------------------------------------------------------
# Abgrenzung: nur Parse-Fehler abfangen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_andere_bad_request_wird_durchgereicht():
    """Ein blockierter Chat oder eine falsche chat_id darf nicht als
    Formatierungsproblem missverstanden werden."""
    senden = AsyncMock(side_effect=BadRequest("Chat not found"))

    with pytest.raises(BadRequest, match="Chat not found"):
        await mit_markdown_fallback(senden, "text", chat_id=1)

    assert senden.await_count == 1


@pytest.mark.asyncio
async def test_netzwerkfehler_wird_durchgereicht():
    senden = AsyncMock(side_effect=TimeoutError())

    with pytest.raises(TimeoutError):
        await mit_markdown_fallback(senden, "text", chat_id=1)


@pytest.mark.asyncio
async def test_zweiter_versuch_scheitert_ebenfalls():
    """Schlägt auch der Klartext fehl, muss der Fehler sichtbar werden –
    nicht still verschluckt."""
    senden = AsyncMock(side_effect=[_parse_fehler(), BadRequest("Chat not found")])

    with pytest.raises(BadRequest, match="Chat not found"):
        await mit_markdown_fallback(senden, "a_b", chat_id=1)


# ---------------------------------------------------------------------------
# Der konkrete Fall: Briefing mit "youtube_agent"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_kommt_trotz_unbalanciertem_unterstrich_an():
    """End-to-End über run_briefing_scheduler wäre eine Endlosschleife –
    daher direkt der Versandpfad, den der Scheduler benutzt."""
    import bot.briefing as briefing_mod

    briefing_text = "*Guten Morgen, Fabio!*\n\n📋 *Offene Punkte:*\n✅ youtube_agent\n"
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[_parse_fehler(), "ok"])

    await briefing_mod._sende_briefing(bot, chat_id=42, briefing=briefing_text)

    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[1].kwargs["text"] == briefing_text
    assert "parse_mode" not in bot.send_message.await_args_list[1].kwargs


# ---------------------------------------------------------------------------
# Strukturschutz: keine ungeschützten Markdown-Sends mehr
# ---------------------------------------------------------------------------


def test_keine_ungeschuetzten_markdown_sends():
    """Jede Stelle, die parse_mode='Markdown' direkt setzt, kann eine Nachricht
    verlieren. Erlaubt ist das nur noch im Helfer selbst."""
    import pathlib

    wurzel = pathlib.Path(__file__).resolve().parent.parent
    treffer = []
    for pfad in (wurzel / "bot").rglob("*.py"):
        if pfad.name == "telegram_markdown.py":
            continue
        for nr, zeile in enumerate(pfad.read_text().split("\n"), 1):
            if 'parse_mode="Markdown"' in zeile:
                treffer.append(f"{pfad.relative_to(wurzel)}:{nr}")

    assert not treffer, "ungeschützter Markdown-Versand:\n  " + "\n  ".join(treffer)
