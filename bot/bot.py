import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from langchain_core.messages import HumanMessage

from bot.auth import restricted
from agent.supervisor import agent_graph

logger = logging.getLogger(__name__)


@restricted
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Mac Agent bereit.\n\n"
        "Schick mir einfach eine Nachricht oder nutze:\n"
        "/ask <Frage> – Direkte Anfrage\n"
        "/status – Agent Status"
    )


@restricted
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Agent läuft.")


@restricted
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Verwendung: /ask <deine Frage>")
        return
    await handle_message_text(update, text)


@restricted
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_message_text(update, update.message.text)


async def handle_message_text(update: Update, text: str):
    thinking = await update.message.reply_text("⏳ Denke nach...")

    try:
        state = {
            "messages": [HumanMessage(content=text)],
            "telegram_chat_id": update.effective_chat.id,
        }
        result = await agent_graph.ainvoke(state)
        response = result["messages"][-1].content
    except Exception as e:
        logger.error(f"Agent error: {e}")
        response = f"❌ Fehler: {e}"

    await thinking.delete()
    await update.message.reply_text(response)


def build_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nicht gesetzt")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
