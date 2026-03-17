import asyncio
import logging
import uuid
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import CallbackQueryHandler, Application

logger = logging.getLogger(__name__)

_pending: dict[str, asyncio.Future] = {}

TIMEOUT_SECONDS = 60


async def request_confirmation(
    bot: Bot,
    chat_id: int,
    agent: str,
    action: str,
) -> bool:
    # Volles UUID statt [:8] – eliminiert Kollisionsrisiko bei parallelen Requests
    confirmation_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _pending[confirmation_id] = future

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Bestaetigen", callback_data=f"confirm:{confirmation_id}"),
            InlineKeyboardButton("Ablehnen", callback_data=f"reject:{confirmation_id}"),
        ]
    ])

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"Bestaetigung erforderlich\n\n"
            f"Agent: {agent}\n"
            f"Aktion: {action}\n\n"
            f"Ausfuehren?"
        ),
        reply_markup=keyboard,
    )

    try:
        result = await asyncio.wait_for(asyncio.shield(future), timeout=TIMEOUT_SECONDS)
        return result
    except asyncio.TimeoutError:
        logger.warning(f"Confirmation timeout fuer id={confirmation_id}")
        _pending.pop(confirmation_id, None)
        await bot.send_message(chat_id=chat_id, text="Timeout - Aktion abgebrochen.")
        return False
    finally:
        _pending.pop(confirmation_id, None)


async def handle_confirmation_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data:
        return

    if data.startswith("confirm:"):
        confirmation_id = data.split(":", 1)[1]
        if confirmation_id in _pending and not _pending[confirmation_id].done():
            _pending[confirmation_id].set_result(True)
            await query.edit_message_text("Bestaetigt - wird ausgefuehrt.")

    elif data.startswith("reject:"):
        confirmation_id = data.split(":", 1)[1]
        if confirmation_id in _pending and not _pending[confirmation_id].done():
            _pending[confirmation_id].set_result(False)
            await query.edit_message_text("Abgelehnt - Aktion abgebrochen.")


def register_confirmation_handler(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_confirmation_callback))