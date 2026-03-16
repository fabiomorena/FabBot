import logging
import os
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from langchain_core.messages import HumanMessage

from bot.auth import restricted
from bot.confirm import request_confirmation, register_confirmation_handler
from bot.transcribe import transcribe_audio
from bot.search import search_knowledge, list_knowledge
from agent.supervisor import agent_graph
from agent.security import sanitize_input
from agent.audit import log_action, log_blocked
from agent.agents.terminal import terminal_agent_execute
from agent.agents.file import file_agent_write
from agent.agents.calendar import calendar_event_create
from agent.agents.computer import computer_agent_execute, _screenshot_to_telegram_bytes
from agent.agents.clip_agent import clip_agent, clip_agent_write

logger = logging.getLogger(__name__)


def _extract_content(msg) -> str:
    """Extrahiert Text aus einer LangChain Message – egal ob str oder list."""
    content = msg.content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return str(content).strip()


@restricted
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Mac Agent bereit.\n\n"
        "Schick mir eine Nachricht oder Sprachnachricht, oder nutze:\n"
        "/ask <Frage> – Direkte Anfrage\n"
        "/clip <URL> – URL als Markdown-Notiz speichern\n"
        "/search <Begriff> – Notizen durchsuchen\n"
        "/search #Tag – Nach Tag suchen\n"
        "/search – Alle Notizen auflisten\n"
        "/status – Agent Status\n"
        "/auditlog – Letzte Aktionen"
    )


@restricted
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Agent laeuft.")


@restricted
async def cmd_auditlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log_path = Path.home() / ".fabbot" / "audit.log"
    if not log_path.exists():
        await update.message.reply_text("Noch keine Aktionen geloggt.")
        return
    lines = log_path.read_text().strip().split("\n")
    last = lines[-10:] if len(lines) > 10 else lines
    await update.message.reply_text("Letzte Aktionen:\n\n" + "\n".join(last))


@restricted
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Verwendung: /ask <deine Frage>")
        return
    await handle_message_text(update, ctx.bot, text)


@restricted
async def cmd_clip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Fetcht eine URL, erstellt eine Markdown-Notiz und speichert sie nach Bestätigung."""
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
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Durchsucht gespeicherte Notizen in ~/Documents/Wissen."""
    chat_id = update.effective_chat.id

    if not ctx.args:
        result = list_knowledge()
    else:
        query = " ".join(ctx.args)
        result = search_knowledge(query)

    await update.message.reply_text(result, parse_mode="Markdown")
    log_action("search", "search_knowledge", " ".join(ctx.args or []), chat_id, status="executed")


@restricted
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_message_text(update, ctx.bot, update.message.text)


@restricted
async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Empfängt Telegram Voice Messages, transkribiert via Whisper und
    leitet den Text an die normale Agent-Pipeline weiter.
    """
    thinking = await update.message.reply_text("Transkribiere...")

    try:
        voice = update.message.voice
        tg_file = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await tg_file.download_as_bytearray()

        text = await transcribe_audio(bytes(audio_bytes))

        if not text:
            await thinking.edit_text(
                "Transkription fehlgeschlagen. Bitte nochmal versuchen."
            )
            return

        await thinking.edit_text(f"_{text}_", parse_mode="Markdown")
        await handle_message_text(update, ctx.bot, text)

    except Exception as e:
        logger.error(f"Voice handler error: {e}", exc_info=True)
        try:
            await thinking.edit_text(
                "Fehler bei der Verarbeitung der Sprachnachricht."
            )
        except Exception:
            pass


async def handle_message_text(update: Update, bot: Bot, text: str):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_safe, result = sanitize_input(text)
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
        result_state = await agent_graph.ainvoke(state, {"recursion_limit": 10})
        response_msg = _extract_content(result_state["messages"][-1])

        # Screenshot senden
        if response_msg.startswith("__SCREENSHOT__:"):
            analysis = response_msg.replace("__SCREENSHOT__:", "")
            await thinking.delete()
            screenshot_bytes = _screenshot_to_telegram_bytes()
            if screenshot_bytes:
                await bot.send_photo(chat_id=chat_id, photo=screenshot_bytes, caption=analysis)
            else:
                await bot.send_message(chat_id=chat_id, text=f"Screenshot-Analyse:\n{analysis}")
            return

        # Human-in-the-Loop: Computer Use Bestaetigung
        if response_msg.startswith("__CONFIRM_COMPUTER__:"):
            parts = response_msg.replace("__CONFIRM_COMPUTER__:", "").split(":", 3)
            action = parts[0] if len(parts) > 0 else ""
            x = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            y = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            text = parts[3] if len(parts) > 3 else ""
            await thinking.delete()
            display = f"{action}: {text}" if text else f"{action} @ ({x}, {y})"
            confirmed = await request_confirmation(bot, chat_id, "computer_agent", display)
            if confirmed:
                output = computer_agent_execute(action, x, y, text, chat_id)
                await bot.send_message(chat_id=chat_id, text=output)
            else:
                log_action("computer_agent", action, "user rejected", chat_id, status="rejected")
            return

        # Human-in-the-Loop: Terminal Bestaetigung
        if response_msg.startswith("__CONFIRM_TERMINAL__:"):
            command = response_msg.split(":", 1)[1]
            await thinking.delete()
            confirmed = await request_confirmation(bot, chat_id, "terminal_agent", command)
            if confirmed:
                output = terminal_agent_execute(command, chat_id)
                await bot.send_message(chat_id=chat_id, text=f"Output:\n\n{output}")
            else:
                log_action("terminal_agent", command, "user rejected", chat_id, status="rejected")
            return

        # Human-in-the-Loop: Calendar Create Bestaetigung
        if response_msg.startswith("__CONFIRM_CREATE_EVENT__:"):
            parts = response_msg.replace("__CONFIRM_CREATE_EVENT__:", "").split("::")
            title = parts[0] if len(parts) > 0 else ""
            start_time = parts[1] if len(parts) > 1 else ""
            end_time = parts[2] if len(parts) > 2 else ""
            await thinking.delete()
            confirmed = await request_confirmation(
                bot, chat_id, "calendar_agent",
                f"Neuer Termin: {title} am {start_time}"
            )
            if confirmed:
                output = calendar_event_create(title, start_time, end_time, chat_id)
                await bot.send_message(chat_id=chat_id, text=output)
            else:
                log_action("calendar_agent", "create_event", f"user rejected: {title}", chat_id, status="rejected")
            return

        # Human-in-the-Loop: File Write Bestaetigung
        if response_msg.startswith("__CONFIRM_FILE_WRITE__:"):
            parts = response_msg.split("::", 1)
            path_str = parts[0].replace("__CONFIRM_FILE_WRITE__:", "")
            file_content = parts[1] if len(parts) > 1 else ""
            await thinking.delete()
            confirmed = await request_confirmation(bot, chat_id, "file_agent", f"Schreibe nach: {path_str}")
            if confirmed:
                output = file_agent_write(Path(path_str), file_content, chat_id)
                await bot.send_message(chat_id=chat_id, text=output)
            else:
                log_action("file_agent", "write", f"user rejected: {path_str}", chat_id, status="rejected")
            return

        await thinking.delete()
        await update.message.reply_text(response_msg or "Keine Antwort vom Agent.")

    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Ein Fehler ist aufgetreten. Bitte versuche es erneut.")


def build_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nicht gesetzt")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auditlog", cmd_auditlog))
    app.add_handler(CommandHandler("ask", cmd_ask, block=False))
    app.add_handler(CommandHandler("clip", cmd_clip, block=False))
    app.add_handler(CommandHandler("search", cmd_search, block=False))
    app.add_handler(MessageHandler(filters.VOICE, on_voice, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message, block=False))
    register_confirmation_handler(app)
    return app