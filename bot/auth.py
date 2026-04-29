import os
import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _load_allowed_ids() -> frozenset[int]:
    """Laedt erlaubte User-IDs aus Env-Var beim Start.

    Phase 84 Fix: Fail-Closed – leere / fehlende Env-Var wirft RuntimeError
    statt frozenset() zurückzugeben. Ein leeres frozenset() wäre zwar
    funktional "blockiert", würde aber den Fehler still verschlucken;
    RuntimeError stoppt den Bot-Start sauber mit einer klaren Fehlermeldung.
    """
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    if not raw.strip():
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_IDS ist nicht gesetzt oder leer – "
            "Bot-Start abgebrochen. Bitte mindestens eine User-ID in .env eintragen."
        )
    ids = frozenset(int(uid.strip()) for uid in raw.split(",") if uid.strip())
    if not ids:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_IDS enthält keine gültige User-ID – "
            "Bot-Start abgebrochen. Bitte mindestens eine gültige Integer-ID eintragen."
        )
    logger.info(f"Bot-Zugriff erlaubt fuer {len(ids)} User-ID(s).")
    return ids


ALLOWED_IDS: frozenset[int] = _load_allowed_ids()


def get_allowed_ids() -> frozenset[int]:
    """Gibt die gecachten erlaubten User-IDs zurueck."""
    return ALLOWED_IDS


def restricted(func):
    """Decorator: blockt alle User die nicht in der Whitelist sind."""

    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs) -> None:
        if update.effective_user is None:
            return
        user_id = update.effective_user.id
        if user_id not in ALLOWED_IDS:
            await update.message.reply_text("⛔ Kein Zugriff.")
            return
        return await func(update, ctx, *args, **kwargs)

    return wrapper
