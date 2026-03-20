import logging
import os
import asyncio
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError, RetryAfter
from langchain_core.messages import HumanMessage
from anthropic import RateLimitError, APIStatusError, APIConnectionError

from bot.auth import restricted
from bot.confirm import request_confirmation, register_confirmation_handler
from bot.transcribe import transcribe_audio
from bot.search import search_knowledge, list_knowledge
from bot.tts import speak_and_send, set_tts_enabled, is_tts_enabled, stop_speaking
from agent.security import sanitize_input_async
from agent.audit import log_action, log_blocked
from agent.protocol import Proto
from agent.agents.terminal import terminal_agent_execute
from agent.agents.file import file_agent_write
from agent.agents.calendar import calendar_event_create
from agent.agents.computer import computer_agent_execute, _screenshot_to_telegram_bytes
from agent.agents.clip_agent import clip_agent, clip_agent_write

logger = logging.getLogger(__name__)

# TTS nur fuer kurze Outputs – lange Dateilisten etc. werden nicht vorgelesen
_TTS_MAX_HITL_OUTPUT = 300


def _extract_content(msg) -> str:
    """Extrahiert Text aus einer LangChain Message – egal ob str oder list."""
    content = msg.content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return str(content).strip()


# ---------------------------------------------------------------------------
# Private HITL-Handler
# ---------------------------------------------------------------------------

async def _handle_screenshot(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    analysis = response_msg[len(Proto.SCREENSHOT):]
    screenshot_bytes = _screenshot_to_telegram_bytes()
    if screenshot_bytes:
        await bot.send_photo(chat_id=chat_id, photo=screenshot_bytes, caption=analysis)
    else:
        await bot.send_message(chat_id=chat_id, text=f"Screenshot-Analyse:\n{analysis}")


async def _handle_confirm_computer(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts = response_msg[len(Proto.CONFIRM_COMPUTER):].split(":", 3)
    action = parts[0] if len(parts) > 0 else ""
    x = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    y = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    text_arg = parts[3] if len(parts) > 3 else ""
    display = f"{action}: {text_arg}" if text_arg else f"{action} @ ({x}, {y})"
    confirmed = await request_confirmation(bot, chat_id, "computer_agent", display)
    if confirmed:
        output = computer_agent_execute(action, x, y, text_arg, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
    else:
        log_action("computer_agent", action, "user rejected", chat_id, status="rejected")


async def _handle_confirm_terminal(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    command = response_msg[len(Proto.CONFIRM_TERMINAL):]
    confirmed = await request_confirmation(bot, chat_id, "terminal_agent", command)
    if confirmed:
        output = terminal_agent_execute(command, chat_id)
        await bot.send_message(chat_id=chat_id, text=f"Output:\n\n{output}")
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
    else:
        log_action("terminal_agent", command, "user rejected", chat_id, status="rejected")


async def _handle_confirm_create_event(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts = response_msg[len(Proto.CONFIRM_CREATE_EVENT):].split("::")
    title = parts[0] if len(parts) > 0 else ""
    start_time = parts[1] if len(parts) > 1 else ""
    end_time = parts[2] if len(parts) > 2 else ""
    confirmed = await request_confirmation(
        bot, chat_id, "calendar_agent",
        f"Neuer Termin: {title} am {start_time}"
    )
    if confirmed:
        output = calendar_event_create(title, start_time, end_time, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
    else:
        log_action("calendar_agent", "create_event", f"user rejected: {title}", chat_id, status="rejected")


async def _handle_confirm_file_write(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts = response_msg[len(Proto.CONFIRM_FILE_WRITE):].split("::", 1)
    path_str = parts[0]
    file_content = parts[1] if len(parts) > 1 else ""
    confirmed = await request_confirmation(bot, chat_id, "file_agent", f"Schreibe nach: {path_str}")
    if confirmed:
        output = file_agent_write(Path(path_str), file_content, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
    else:
        log_action("file_agent", "write", f"user rejected: {path_str}", chat_id, status="rejected")


_RESPONSE_DISPATCH: list[tuple[str, callable]] = [
    (Proto.SCREENSHOT,           _handle_screenshot),
    (Proto.CONFIRM_COMPUTER,     _handle_confirm_computer),
    (Proto.CONFIRM_TERMINAL,     _handle_confirm_terminal),
    (Proto.CONFIRM_CREATE_EVENT, _handle_confirm_create_event),
    (Proto.CONFIRM_FILE_WRITE,   _handle_confirm_file_write),
]


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

@restricted
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Mac Agent bereit.\n\n"
        "Schick mir eine Nachricht oder Sprachnachricht, oder nutze:\n"
        "/ask <Frage> – Direkte Anfrage\n"
        "/clip <URL> – URL als Markdown-Notiz speichern\n"
        "/search <Begriff> – Notizen durchsuchen\n"
        "/search #Tag – Nach Tag suchen\n"
        "/search – Alle Notizen auflisten\n"
        "/tts on|off – Sprachausgabe aktivieren/deaktivieren\n"
        "/stop – Laufende Sprachausgabe stoppen\n"
        "/status – Agent Status\n"
        "/auditlog – Letzte Aktionen"
    )


@restricted
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tts_status = "aktiviert" if is_tts_enabled() else "deaktiviert"
    await update.message.reply_text(f"Agent laeuft. TTS: {tts_status}")


@restricted
async def cmd_auditlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log_path = Path.home() / ".fabbot" / "audit.log"
    if not log_path.exists():
        await update.message.reply_text("Noch keine Aktionen geloggt.")
        return
    lines = log_path.read_text().strip().split("\n")
    last = lines[-10:] if len(lines) > 10 else lines
    await update.message.reply_text("Letzte Aktionen:\n\n" + "\n".join(last))


@restricted
async def cmd_tts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Aktiviert oder deaktiviert TTS zur Laufzeit."""
    if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
        status = "aktiviert" if is_tts_enabled() else "deaktiviert"
        await update.message.reply_text(
            f"Sprachausgabe ist aktuell {status}.\n"
            "Verwendung: /tts on oder /tts off"
        )
        return
    enabled = ctx.args[0].lower() == "on"
    set_tts_enabled(enabled)
    status = "aktiviert" if enabled else "deaktiviert"
    await update.message.reply_text(f"Sprachausgabe {status}.")


@restricted
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stoppt die laufende Sprachausgabe sofort."""
    stopped = stop_speaking()
    if stopped:
        await update.message.reply_text("Sprachausgabe gestoppt.")
    else:
        await update.message.reply_text("Keine laufende Sprachausgabe.")


@restricted
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Verwendung: /ask <deine Frage>")
        return
    await handle_message_text(update, ctx.bot, text)


@restricted
async def cmd_clip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Verwendung: /clip <URL>\n"
            "Beispiel: /clip https://example.com/artikel"
        )
        return

    url = ctx.args[0]
    chat_id = update.effective_chat.id
    thinking = await update.message.reply_text(f"Lese {url} ...")

    result = await clip_agent(url, chat_id)

    if not result["ok"]:
        await thinking.edit_text(f"Fehler: {result['error']}")
        return

    await thinking.edit_text(
        f"Vorschau:\n\n{result['preview']}\n\n"
        f"Speichern als: {result['filename']}"
    )

    confirmed = await request_confirmation(
        ctx.bot, chat_id, "clip_agent",
        f"Speichern: {result['filename']}"
    )

    if confirmed:
        output = clip_agent_write(result["path"], result["content"], chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=output)
    else:
        log_action("clip_agent", "write", f"user rejected: {result['filename']}", chat_id, status="rejected")


@restricted
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not ctx.args:
        result = list_knowledge()
    else:
        query = " ".join(ctx.args)
        result = search_knowledge(query)
    await update.message.reply_text(result, parse_mode="Markdown")
    log_action("search", "search_knowledge", " ".join(ctx.args or []), chat_id, status="executed")


@restricted
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_message_text(update, ctx.bot, update.message.text)


@restricted
async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    thinking = await update.message.reply_text("Transkribiere...")
    try:
        voice = update.message.voice
        tg_file = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await tg_file.download_as_bytearray()
        text = await transcribe_audio(bytes(audio_bytes))

        if not text:
            await thinking.edit_text("Transkription fehlgeschlagen. Bitte nochmal versuchen.")
            return

        await thinking.edit_text(f"_{text}_", parse_mode="Markdown")
        await handle_message_text(update, ctx.bot, text)

    except (TimedOut, NetworkError) as e:
        logger.warning(f"Telegram network error in voice handler: {e}")
        try:
            await thinking.edit_text("Netzwerkfehler – bitte nochmal versuchen.")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Voice handler error: {e}", exc_info=True)
        try:
            await thinking.edit_text("Fehler bei der Verarbeitung der Sprachnachricht.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------

async def handle_message_text(update: Update, bot: Bot, text: str) -> None:
    """Verarbeitet eingehende Textnachrichten durch den Agent-Graph."""
    from agent.supervisor import agent_graph
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_safe, result = await sanitize_input_async(text, user_id)
    if not is_safe:
        log_blocked(result, text, user_id)
        await update.message.reply_text(f"Eingabe abgelehnt: {result}")
        return

    clean_text = result
    thinking = await update.message.reply_text("Denke nach...")

    try:
        state = {
            "messages": [HumanMessage(content=clean_text)],
            "telegram_chat_id": chat_id,
            "next_agent": None,
        }

        result_state = await agent_graph.ainvoke(
            state,
            config={"configurable": {"thread_id": str(chat_id)}, "recursion_limit": 10},
        )
        response_msg = _extract_content(result_state["messages"][-1])

        for prefix, handler in _RESPONSE_DISPATCH:
            if response_msg.startswith(prefix):
                await thinking.delete()
                await handler(response_msg=response_msg, bot=bot, chat_id=chat_id)
                return

        await thinking.delete()

        await asyncio.gather(
            update.message.reply_text(response_msg or "Keine Antwort vom Agent."),
            speak_and_send(response_msg, bot, chat_id) if response_msg else asyncio.sleep(0),
        )

    except RateLimitError:
        logger.warning(f"Anthropic rate limit hit for user={user_id}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Zu viele Anfragen – bitte kurz warten und nochmal versuchen.")

    except APIConnectionError as e:
        logger.warning(f"Anthropic connection error: {e}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Verbindungsfehler zur KI – bitte nochmal versuchen.")

    except APIStatusError as e:
        logger.error(f"Anthropic API status error: {e.status_code} {e.message}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text(f"API Fehler ({e.status_code}) – bitte Administrator informieren.")

    except (TimedOut, NetworkError) as e:
        logger.warning(f"Telegram network error: {e}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Netzwerkfehler – bitte nochmal versuchen.")

    except RetryAfter as e:
        logger.warning(f"Telegram rate limit, retry after {e.retry_after}s")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text(f"Telegram meldet: bitte {e.retry_after}s warten.")

    except asyncio.TimeoutError:
        logger.warning(f"LLM timeout for user={user_id}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Timeout – die Anfrage hat zu lange gedauert. Bitte nochmal versuchen.")

    except Exception as e:
        logger.error(f"Unexpected agent error: {e}", exc_info=True)
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Ein unerwarteter Fehler ist aufgetreten. Bitte versuche es erneut.")


# ---------------------------------------------------------------------------
# Lifecycle Hooks
# ---------------------------------------------------------------------------

async def _post_init(app: Application) -> None:
    """Initialisiert AsyncSqliteSaver nachdem der Event Loop gestartet ist."""
    from agent.supervisor import init_graph
    await init_graph()
    logger.info("SqliteSaver-Checkpointer initialisiert.")


async def _post_shutdown(app: Application) -> None:
    """Schliesst die SQLite-Verbindung sauber beim Shutdown."""
    from agent.supervisor import close_graph
    await close_graph()
    logger.info("SqliteSaver-Verbindung geschlossen.")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

def build_bot() -> Application:
    """Erstellt und konfiguriert die Telegram-Bot-Application."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nicht gesetzt")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auditlog", cmd_auditlog))
    app.add_handler(CommandHandler("tts", cmd_tts))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("ask", cmd_ask, block=False))
    app.add_handler(CommandHandler("clip", cmd_clip, block=False))
    app.add_handler(CommandHandler("search", cmd_search, block=False))
    app.add_handler(MessageHandler(filters.VOICE, on_voice, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message, block=False))
    register_confirmation_handler(app)
    return app
