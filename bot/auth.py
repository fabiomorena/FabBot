import os
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes


def get_allowed_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    return {int(uid.strip()) for uid in raw.split(",") if uid.strip()}


def restricted(func):
    """Decorator: blockt alle User die nicht in der Whitelist sind."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in get_allowed_ids():
            await update.message.reply_text("⛔ Kein Zugriff.")
            return
        return await func(update, ctx, *args, **kwargs)
    return wrapper
