"""
bot/flug/loeschen.py – Phase 247 (Issue #385)

Löschen von Suchen – als eigener Zustand im Dialog, damit auch getippte
Antworten ankommen, nicht nur Button-Klicks (Live-Test 20.11.2026: "ja" und
"weg" verpufften, der Button war nicht als Button zu erkennen).

- eine Suche: "Soll diese Suche weg?" mit [Ja, löschen] [Nein] – oder ja/nein tippen
- mehrere: nummerierte Liste – Nummer, Zielname oder Button
- direkt: "/löschen Lissabon", wenn genau eine Suche passt

Gelöscht wird immer nur über store.entferne(), das die Eigentümerschaft prüft.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.flug import store
from bot.flug.bereinigung import einzeilig
from bot.flug.modelle import Watch
from bot.flug.texte import watch_text, zeitraum_text
from bot.flug.tg_hilfen import antworte, argument, eingabe_text, klick, nutzer_id, nutzerdaten, tastatur_zeilen

logger = logging.getLogger(__name__)

LOESCHEN = 6  # Zustand im Dialog-ConversationHandler (ZIEL … PREIS = 0 … 5)
_KEY = "flug_loeschen"
_JA = frozenset({"ja", "j", "jo", "jep", "yes", "y", "ok", "okay", "weg", "löschen", "loeschen", "lösch", "si", "sì"})
_NEIN = frozenset({"nein", "n", "no", "nö", "nee", "abbrechen", "behalten", "doch nicht"})


def _norm(text: str) -> str:
    return " ".join(text.lower().strip().strip(".!?").split())


def finde(eingabe: str, watches: tuple[Watch, ...]) -> tuple[Watch, ...]:
    """Suchen zu einer Antwort: "ja" (nur bei genau einer), Nummer oder Zielname/-code."""
    norm = _norm(eingabe)
    if not norm:
        return ()
    if norm in _JA:
        return watches if len(watches) == 1 else ()
    if norm.isdigit():
        nummer = int(norm)
        return (watches[nummer - 1],) if 1 <= nummer <= len(watches) else ()
    return tuple(w for w in watches if norm == w.ziel.code.lower() or w.ziel.name.lower().startswith(norm))


def _frage_und_knoepfe(kandidaten: tuple[Watch, ...]) -> tuple[str, list[list[tuple[str, str]]]]:
    if len(kandidaten) == 1:
        (w,) = kandidaten
        text = f"Soll diese Suche weg?\n\n{watch_text(w)}\n\nTipp auf einen Button oder antworte mit „ja“ oder „nein“."
        return text, [[("🗑 Ja, löschen", f"del:{w.id}"), ("Nein", "del:nein")]]
    liste = "\n\n".join(f"{i}. {watch_text(w)}" for i, w in enumerate(kandidaten, start=1))
    text = f"Welche Suche soll weg?\n\n{liste}\n\nTipp auf einen Button oder antworte mit der Nummer."
    knoepfe = [
        [(f"🗑 {i}. {w.ziel.name}, {zeitraum_text(w.von, w.bis)}", f"del:{w.id}")]
        for i, w in enumerate(kandidaten, start=1)
    ]
    return text, [*knoepfe, [("Nichts löschen", "del:nein")]]


async def _frage(update: Update, ctx: ContextTypes.DEFAULT_TYPE, kandidaten: tuple[Watch, ...]) -> int:
    nutzerdaten(ctx)[_KEY] = tuple(w.id for w in kandidaten)
    text, knoepfe = _frage_und_knoepfe(kandidaten)
    await antworte(update, text, reply_markup=tastatur_zeilen(knoepfe))
    return LOESCHEN


async def _entferne(update: Update, ctx: ContextTypes.DEFAULT_TYPE, watch_id: str) -> str:
    """Löscht und liefert den Antworttext."""
    nutzerdaten(ctx)[_KEY] = ()
    try:
        entfernt = await store.entferne(nutzer_id(update), watch_id)
    except (OSError, store.StoreFehler) as e:
        logger.error(f"Flug-Bot: Löschen fehlgeschlagen ({type(e).__name__})")
        return "Löschen hat nicht geklappt. Bitte versuch es später nochmal."
    if entfernt is None:
        return "Diese Suche gibt es nicht (mehr)."
    return f"🗑 gelöscht:\n{watch_text(entfernt)}"


async def cmd_loeschen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    """/loeschen bzw. /löschen [Name]. None = Zustand bleibt (nichts zu löschen)."""
    watches = store.watches_von(nutzer_id(update))
    if not watches:
        await antworte(update, "Du hast keine aktiven Suchen.")
        return None
    wunsch = argument(update)
    if not wunsch:
        return await _frage(update, ctx, watches)
    treffer = finde(wunsch, watches)
    if len(treffer) == 1:
        await antworte(update, await _entferne(update, ctx, treffer[0].id))
        return ConversationHandler.END
    if not treffer:
        await antworte(update, f"Keine Suche passt zu „{einzeilig(wunsch, 40)}“.")
    return await _frage(update, ctx, treffer or watches)


async def on_loeschen_antwort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Getippte Antwort im Löschdialog. None = weiter auf eine Antwort warten."""
    ids = nutzerdaten(ctx).get(_KEY, ())
    eigene = {w.id: w for w in store.watches_von(nutzer_id(update))}
    kandidaten = tuple(eigene[i] for i in ids if i in eigene)
    if not kandidaten:
        await antworte(update, "Diese Suchen gibt es nicht mehr.")
        return ConversationHandler.END

    norm = _norm(eingabe_text(update))
    if norm in _NEIN:
        nutzerdaten(ctx)[_KEY] = ()
        await antworte(update, "Okay, nichts gelöscht.")
        return ConversationHandler.END
    if norm in _JA and len(kandidaten) > 1:
        await antworte(update, f"Welche denn? Antworte mit der Nummer (1–{len(kandidaten)}) oder „nein“.")
        return None

    treffer = finde(norm, kandidaten)
    if len(treffer) == 1:
        await antworte(update, await _entferne(update, ctx, treffer[0].id))
        return ConversationHandler.END
    if treffer:
        return await _frage(update, ctx, treffer)
    hinweis = "„ja“ oder „nein“" if len(kandidaten) == 1 else f"einer Nummer von 1 bis {len(kandidaten)} oder „nein“"
    await antworte(update, f"Das habe ich nicht verstanden. Antworte mit {hinweis}.")
    return None


async def on_loeschen_klick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Button [Ja, löschen] / [Nummer …] / [Nein] – auch auf älteren Nachrichten."""
    query = klick(update)
    await query.answer()
    wahl = (query.data or "").partition(":")[2]
    if wahl == "nein":
        nutzerdaten(ctx)[_KEY] = ()
        await query.edit_message_text("Okay, nichts gelöscht.")
        return ConversationHandler.END
    await query.edit_message_text(await _entferne(update, ctx, wahl))
    return ConversationHandler.END
