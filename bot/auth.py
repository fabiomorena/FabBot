import os
import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _load_allowed_ids() -> frozenset[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    if not raw.strip():
        logger.critical(
            "TELEGRAM_ALLOWED_USER_IDS ist nicht gesetzt oder leer – "
            "niemand kann den Bot benutzen!"
        )
        return frozenset()
    ids = frozenset(int(uid.strip()) for uid in raw.split(",") if uid.strip())
    logger.info(f"Bot-Zugriff erlaubt fuer {len(ids)} User-ID(s).")
    return ids


# Einmalig beim Start laden – nicht bei jeder Nachricht neu parsen
ALLOWED_IDS: frozenset[int] = _load_allowed_ids()


def get_allowed_ids() -> frozenset[int]:
    """Gibt die gecachten erlaubten User-IDs zurück."""
    return ALLOWED_IDS


def restricted(func):
    """Decorator: blockt alle User die nicht in der Whitelist sind."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_IDS:
            await update.message.reply_text("⛔ Kein Zugriff.")
            return
        return await func(update, ctx, *args, **kwargs)
    return wrapper
