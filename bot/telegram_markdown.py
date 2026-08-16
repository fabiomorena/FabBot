"""
Markdown-Versand mit Klartext-Fallback.

Telegrams Legacy-Markdown lehnt eine Nachricht **vollständig** ab, sobald ein
`*`, `_` oder Backtick unbalanciert ist:

    Can't parse entities: can't find end of the entity starting at byte offset 360

Am 16.08.2026 fiel dadurch das Morning Briefing aus. Auslöser war ein Pending
Item namens "youtube_agent" – ein einzelner Unterstrich, der eine Kursiv-Entity
öffnete und nie schloss. Das Briefing war da bereits fertig generiert.

Der Text dieser Nachrichten stammt fast vollständig aus fremder Quelle:
RSS-Schlagzeilen, Kalendertermine, Erinnerungen im Wortlaut des Users,
Exception-Meldungen. Er lässt sich nicht zuverlässig auf balanciertes Markdown
trimmen – Escaping für Legacy-Markdown ist fehleranfällig. Deshalb der Weg über
einen zweiten Versuch ohne `parse_mode`: die Formatierung geht verloren, der
Inhalt kommt an. Bei proaktiven Nachrichten merkt sonst niemand den Ausfall.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from telegram.error import BadRequest

logger = logging.getLogger(__name__)

# Telegram formuliert Parse-Fehler uneinheitlich ("can't parse entities",
# "can't find end of the entity", "unsupported start tag") – gemeinsam ist der
# Bezug auf das Parsen der Formatierung.
_PARSE_MARKER = ("parse entities", "entity", "unsupported start tag", "can't parse")


def _ist_parse_fehler(e: BadRequest) -> bool:
    text = str(e).lower()
    return any(marker in text for marker in _PARSE_MARKER)


async def mit_markdown_fallback(
    senden: Callable[..., Awaitable[Any]],
    text: str,
    **kwargs: Any,
) -> Any:
    """Sendet `text` als Markdown; bei Parse-Fehler unformatiert erneut.

    `senden` ist die aufzurufende Coroutine-Funktion – etwa `bot.send_message`,
    `update.message.reply_text` oder `message.edit_text`. Der Text wird immer
    als Keyword übergeben, damit derselbe Aufruf für beide Versuche taugt.

    Andere Fehler (blockierter Chat, unbekannte chat_id, Netzwerk) werden
    unverändert weitergereicht – nur Formatierungsprobleme rechtfertigen einen
    zweiten Versuch.
    """
    try:
        return await senden(text=text, parse_mode="Markdown", **kwargs)
    except BadRequest as e:
        if not _ist_parse_fehler(e):
            raise
        logger.warning(f"Markdown-Parse-Fehler ({e}) – sende unformatiert nach: {text[:60]!r}")
        return await senden(text=text, **kwargs)
