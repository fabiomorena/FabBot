"""
tests/test_ph233_briefing_escaping.py – Phase 233 (Issue #329)

Der Fallback aus Phase 232 rettete das Briefing, griff aber ab dem 17.08.2026
JEDEN Morgen: ein Pending Item namens "youtube_agent" brach das Markdown, und
der Nutzer bekam elf Tage lang ein unformatiertes Briefing – keine fetten
Überschriften, keine Struktur.

Der Fallback ist ein Sicherheitsnetz, kein Dauerzustand. Die Ursache liegt
davor: Fremdtext (Pending Items, RSS-Schlagzeilen, Kalendertermine, Wetter)
wird ungeschützt in ein Markdown-Template eingesetzt. Escaping an dieser Stelle
erhält die Formatierung, egal wie ein Eintrag heißt.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bot.briefing as bm


def _unescaped(text: str, zeichen: str) -> int:
    """Zählt Vorkommen von `zeichen`, die NICHT mit Backslash escapt sind."""
    treffer = 0
    for i, c in enumerate(text):
        if c == zeichen and (i == 0 or text[i - 1] != "\\"):
            treffer += 1
    return treffer


def _ist_balanciert(text: str) -> bool:
    """Telegram Legacy-Markdown: unescapte * _ ` müssen paarig sein."""
    return all(_unescaped(text, z) % 2 == 0 for z in ("*", "_", "`"))


async def _briefing_mit(weather="⛅ 20°C", calendar="Keine Termine heute.", pending="", news="• Nachricht"):
    """Baut ein Briefing mit kontrollierten Sections."""
    sections = {"weather": weather, "calendar": calendar, "pending": pending, "news": news}
    with (
        patch("bot.briefing.orchestrate_briefing", new=AsyncMock(return_value=sections)),
        patch("bot.briefing.get_pending_items", return_value=[]),
    ):
        return await bm.generate_briefing()


# ---------------------------------------------------------------------------
# Der konkrete Fall: youtube_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_item_mit_unterstrich_bricht_das_briefing_nicht():
    """Der Auslöser vom 16.08.2026 – seither griff der Fallback täglich."""
    briefing = await _briefing_mit(pending="✅ youtube_agent")

    assert _ist_balanciert(briefing), "unbalanciertes Markdown – der Fallback müsste wieder greifen"
    assert "youtube" in briefing, "der Eintrag muss noch lesbar sein"


@pytest.mark.asyncio
async def test_ueberschriften_bleiben_formatiert():
    """Der eigentliche Zweck: die Struktur des Briefings überlebt."""
    briefing = await _briefing_mit(pending="✅ youtube_agent")

    assert "*Guten Morgen, Fabio!*" in briefing
    assert "*Wetter Berlin:*" in briefing
    assert "*Top News:*" in briefing


# ---------------------------------------------------------------------------
# Alle vier Fremdtext-Quellen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("feld", ["weather", "calendar", "pending", "news"])
async def test_jede_section_wird_escaped(feld):
    """RSS-Schlagzeilen und Kalendertitel sind genauso wenig kontrollierbar
    wie Pending Items."""
    briefing = await _briefing_mit(**{feld: "kaputt_es *Zeug` hier"})

    assert _ist_balanciert(briefing), f"Section '{feld}' bricht das Markdown"


@pytest.mark.asyncio
async def test_mehrere_sonderzeichen_gleichzeitig():
    briefing = await _briefing_mit(
        calendar="10:00 Meeting mit *Marco*",
        pending="✅ youtube_agent\n💭 test_case_2",
        news="• Titel mit _Kursiv_ und `Code`",
    )

    assert _ist_balanciert(briefing)


@pytest.mark.asyncio
async def test_text_bleibt_lesbar():
    """Escaping darf den Inhalt nicht zerstören – nur maskieren."""
    briefing = await _briefing_mit(pending="✅ youtube_agent")

    entschaerft = briefing.replace("\\", "")
    assert "youtube_agent" in entschaerft


@pytest.mark.asyncio
async def test_harmloser_text_bleibt_unveraendert():
    """Ohne Sonderzeichen darf kein Backslash auftauchen."""
    briefing = await _briefing_mit(
        weather="20 Grad, sonnig",
        calendar="Keine Termine heute.",
        pending="",
        news="• Alles ruhig",
    )

    assert "\\" not in briefing


# ---------------------------------------------------------------------------
# Zusammenspiel mit dem Fallback aus Phase 232
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_greift_nicht_mehr():
    """Das eigentliche Ziel: Das Briefing kommt FORMATIERT an, der Fallback
    aus Phase 232 bleibt ein Notnagel für den Rest."""
    from telegram.error import BadRequest

    briefing = await _briefing_mit(pending="✅ youtube_agent")

    versuche = []

    async def fake_send(**kw):
        versuche.append(kw)
        # Telegram lehnt nur bei unbalanciertem Markdown ab
        if kw.get("parse_mode") == "Markdown" and not _ist_balanciert(kw["text"]):
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return "ok"

    bot = AsyncMock()
    bot.send_message = fake_send
    await bm._sende_briefing(bot, chat_id=1, briefing=briefing)

    assert len(versuche) == 1, "Fallback wurde ausgelöst – Escaping hat nicht gegriffen"
    assert versuche[0]["parse_mode"] == "Markdown"
