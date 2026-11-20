"""
bot/flug/tg_hilfen.py – Phase 247 (Issue #385)

Kleine Telegram-Helfer, die Dialog und Löschen gemeinsam nutzen.

PTB tippt Nachricht, Nutzer, Chat, Klick und user_data als Optional. Für die
Handler des Flug-Bots sind sie immer gesetzt (Schranke + Handler-Filter);
die Zugriffs-Helfer grenzen den Typ ein und werfen sonst einen klaren Fehler,
den der Error-Handler protokolliert – statt eines assert, das mit -O entfiele.
"""

from typing import Any

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes


class UnvollstaendigesUpdate(RuntimeError):
    """Update ohne ein Feld, das der Handler zwingend braucht."""


def nachricht(update: Update) -> Message:
    if update.effective_message is None:
        raise UnvollstaendigesUpdate("Update ohne Nachricht")
    return update.effective_message


def nutzer_id(update: Update) -> int:
    if update.effective_user is None:
        raise UnvollstaendigesUpdate("Update ohne Nutzer")
    return update.effective_user.id


def chat_id(update: Update) -> int:
    if update.effective_chat is None:
        raise UnvollstaendigesUpdate("Update ohne Chat")
    return update.effective_chat.id


def klick(update: Update) -> CallbackQuery:
    if update.callback_query is None:
        raise UnvollstaendigesUpdate("Update ohne Button-Klick")
    return update.callback_query


def nutzerdaten(ctx: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
    if ctx.user_data is None:
        raise UnvollstaendigesUpdate("Kontext ohne user_data")
    return ctx.user_data


def eingabe_text(update: Update) -> str:
    return (nachricht(update).text or "").strip()


def argument(update: Update) -> str:
    """Text nach dem Befehl: "/löschen Lissabon" → "Lissabon"."""
    teile = eingabe_text(update).split(maxsplit=1)
    return teile[1].strip() if len(teile) > 1 else ""


async def antworte(update: Update, text: str, **kwargs: Any) -> None:
    await nachricht(update).reply_text(text, **kwargs)


def tastatur_zeilen(zeilen: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Buttons zeilenweise: [[(Text, callback_data), …], …]."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in z] for z in zeilen])


def tastatur(knoepfe: list[tuple[str, str]], pro_zeile: int = 1) -> InlineKeyboardMarkup:
    return tastatur_zeilen([knoepfe[i : i + pro_zeile] for i in range(0, len(knoepfe), pro_zeile)])
