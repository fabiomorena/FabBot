"""
bot/bot.py – Telegram Handler für FabBot.

Phase 107: _invoke_and_extract fix – last_human_idx statt input_count.
- input_count=1 (nur neue HumanMessage im state) aber result_state enthält
  kompletten LangGraph-Checkpoint → result_state["messages"][1:] liefert
  alte AIMessages bei FINISH → Duplikate.
- Fix: letzte HumanMessage als Ankerpunkt – nur AIMessages danach sind neu.
- _dispatch_response: bei leerem response_msg nichts senden (stilles FINISH).

Phase 104 (Issue #16b): _invoke_locks – serialisiert concurrent Graph-Calls pro chat_id.
- block=False erlaubt parallele Handler – ohne Lock können zwei schnelle Messages
  gleichzeitig ainvoke() auf denselben thread_id aufrufen → Race Condition im
  LangGraph SQLite-Checkpointer → doppelte Antworten.
- _get_invoke_lock(chat_id) gibt pro chat_id einen asyncio.Lock zurück.
- handle_message_text wraps _invoke_and_extract mit diesem Lock.

Phase 97 (Issue #10): _processed_message_ids deque(maxlen=200)
- _is_duplicate(update) zentraler Helper
- Aufruf in on_message, on_voice, on_photo, on_document
- FIFO-Semantik – älteste IDs automatisch verdrängt, kein unbegrenztes Wachstum
- In-Memory reicht, kein SQLite nötig

Phase 95 (Issue #6): validate_models_on_startup() in _post_init() aufgerufen.
Phase 92: setup_audit_logger() in _post_init() aufgerufen.
Phase 91: asyncio.create_task(index_file(...)) in cmd_clip ohne Referenz-Haltung gefixt.
Phase 84 Änderungen: handle_message_text aufgeteilt, _delete_thinking mit suppress, etc.
"""
import contextlib
import logging
import os
import asyncio
from collections import deque
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError, RetryAfter
from langchain_core.messages import HumanMessage, AIMessage
from anthropic import RateLimitError, APIStatusError, APIConnectionError
from langgraph.errors import GraphRecursionError

from bot.auth import restricted
from bot.confirm import request_confirmation, register_confirmation_handler
from bot.transcribe import transcribe_audio
from bot.search import search_knowledge, list_knowledge
from bot.tts import speak_and_send, set_tts_enabled, is_tts_enabled, stop_speaking
from agent.security import sanitize_input_async, check_action_rate_limit
from agent.audit import log_action, log_blocked
from agent.protocol import Proto
from agent.agents.terminal import terminal_agent_execute
from agent.agents.file import file_agent_write
from agent.agents.calendar import calendar_event_create
from agent.agents.computer import computer_agent_execute, _screenshot_to_telegram_bytes
from agent.agents.clip_agent import clip_agent, clip_agent_write
from agent.agents.vision_agent import analyze_image_direct
from bot.whatsapp import (
    send_whatsapp_message, is_session_ready,
    add_whatsapp_contact, remove_whatsapp_contact, list_whatsapp_contacts_formatted,
    get_service_status, get_qr_code, start_service, stop_service,
)

logger = logging.getLogger(__name__)

_TTS_MAX_HITL_OUTPUT = 300

_IMAGE_MAX_PX    = 1920
_IMAGE_MAX_BYTES = 5_000_000  # 5 MB

# Phase 91: Task-Registry für Background-Tasks in cmd_clip.
_background_tasks: set[asyncio.Task] = set()

# Issue #10: Dedup-Store – verhindert Doppelverarbeitung bei Telegram-Retries.
# deque(maxlen=200): FIFO-Semantik, älteste IDs werden automatisch verdrängt.
# In-Memory reicht – nach Neustart sind pending Retries irrelevant.
_processed_message_ids: deque[int] = deque(maxlen=200)
_dedup_lock: asyncio.Lock | None = None

# Phase 104 (Issue #16b): Pro chat_id ein Lock – verhindert concurrent ainvoke()
# auf denselben LangGraph thread_id (Race Condition im SQLite-Checkpointer).
_invoke_locks: dict[int, asyncio.Lock] = {}


def _get_dedup_lock() -> asyncio.Lock:
    global _dedup_lock
    if _dedup_lock is None:
        _dedup_lock = asyncio.Lock()
    return _dedup_lock


def _get_invoke_lock(chat_id: int) -> asyncio.Lock:
    return _invoke_locks.setdefault(chat_id, asyncio.Lock())


def _resize_image(img_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Skaliert Bild auf max. 1920px falls nötig."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        if max(img.width, img.height) <= _IMAGE_MAX_PX:
            return img_bytes, mime_type
        img.thumbnail((_IMAGE_MAX_PX, _IMAGE_MAX_PX), Image.LANCZOS)
        output = io.BytesIO()
        fmt = "PNG" if mime_type == "image/png" else "JPEG"
        save_kwargs = {"optimize": True}
        if fmt == "JPEG":
            save_kwargs["quality"] = 90
        if img.mode in ("RGBA", "P") and fmt == "JPEG":
            img = img.convert("RGB")
        img.save(output, format=fmt, **save_kwargs)
        result = output.getvalue()
        logger.info(f"Bild skaliert: {img.width}x{img.height}px, {len(img_bytes)}b → {len(result)}b")
        return result, mime_type
    except Exception as e:
        logger.warning(f"Bild-Resize fehlgeschlagen (Original wird verwendet): {e}")
        return img_bytes, mime_type


async def _is_duplicate(update: Update) -> bool:
    """Issue #10: Prüft ob diese Message-ID bereits verarbeitet wurde.

    Verhindert Doppelverarbeitung bei Telegram-Retries (z.B. nach Timeout).
    FIFO-Semantik via deque(maxlen=200) – kein unbegrenztes Wachstum.
    """
    msg = update.message
    if msg is None:
        return False
    msg_id = msg.message_id
    async with _get_dedup_lock():
        if msg_id in _processed_message_ids:
            logger.debug(f"Duplikat ignoriert: message_id={msg_id}")
            return True
        _processed_message_ids.append(msg_id)
    return False


_scheduler_tasks: list = []

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY   = 2.0


def _extract_content(msg) -> str:
    content = msg.content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return str(content).strip()


async def _update_memory(chat_id: int, result_text: str) -> None:
    try:
        from agent.supervisor import get_graph
        config = {"configurable": {"thread_id": str(chat_id)}}
        await get_graph().aupdate_state(
            config,
            {"messages": [AIMessage(content=f"__MEMORY__:{result_text}")]},
        )
    except Exception as e:
        logger.warning(f"Memory update nach HITL fehlgeschlagen (nicht kritisch): {e}")


async def _update_vision_memory(chat_id: int, caption: str, result: str) -> None:
    try:
        from agent.supervisor import get_graph
        from langchain_core.messages import HumanMessage as HM
        config     = {"configurable": {"thread_id": str(chat_id)}}
        human_text = f"[Foto] {caption}" if caption else "[Foto gesendet]"
        await get_graph().aupdate_state(
            config,
            {"messages": [HM(content=human_text), AIMessage(content=result)]},
            as_node="supervisor",
        )
    except Exception as e:
        logger.warning(f"Vision memory update fehlgeschlagen: {e}", exc_info=True)


_TRANSIENT_EXCEPTIONS = (APIConnectionError, RateLimitError)


async def _invoke_with_retry(state: dict, config: dict) -> dict:
    from agent.supervisor import get_graph
    last_exception = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return await get_graph().ainvoke(state, config=config)
        except _TRANSIENT_EXCEPTIONS as e:
            # Vor APIStatusError prüfen – RateLimitError ist Subklasse von APIStatusError
            last_exception = e
        except APIStatusError as e:
            if e.status_code == 529:
                last_exception = e
            else:
                raise

        delay = _RETRY_BASE_DELAY * (2 ** attempt)
        logger.warning(
            f"{type(last_exception).__name__} – Versuch {attempt + 1}/{_RETRY_MAX_ATTEMPTS}, "
            f"warte {delay:.0f}s..."
        )
        if attempt < _RETRY_MAX_ATTEMPTS - 1:
            await asyncio.sleep(delay)

    raise last_exception


# ---------------------------------------------------------------------------
# Phase 84: Shared Helpers
# ---------------------------------------------------------------------------

async def _delete_thinking(thinking) -> None:
    with contextlib.suppress(Exception):
        await thinking.delete()


async def _sanitize_and_validate(text: str, user_id: int, update: Update) -> tuple[bool, str]:
    is_safe, result = await sanitize_input_async(text, user_id)
    if not is_safe:
        log_blocked(result, text, user_id)
        await update.message.reply_text(f"Eingabe abgelehnt: {result}")
    return is_safe, result


async def _invoke_and_extract(state: dict, config: dict) -> str:
    """Phase 107: last_human_idx als Ankerpunkt statt input_count.

    input_count=1 (nur neue HumanMessage in state["messages"]) aber
    result_state enthält den kompletten LangGraph-Checkpoint. Bei FINISH
    gibt es keine neuen AIMessages, aber result_state["messages"][1:]
    enthält alte AIMessages → Duplikate.

    Fix: letzte HumanMessage im result_state als Ankerpunkt suchen –
    nur AIMessages danach (ohne __MEMORY__) sind die neue Antwort.
    """
    result_state = await _invoke_with_retry(state, config)
    messages = result_state["messages"]

    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    ai_messages = [
        m for m in messages[last_human_idx + 1:]
        if isinstance(m, AIMessage)
        and not str(m.content).startswith("__MEMORY__")
    ]
    response_msg = _extract_content(ai_messages[-1]) if ai_messages else ""
    if not response_msg:
        logger.debug("_invoke_and_extract: keine neue AI-Antwort (FINISH oder leer)")
    return response_msg


async def _dispatch_response(response_msg: str, bot: Bot, chat_id: int, update: Update) -> None:
    # Phase 107: leere Antwort = FINISH – nichts senden, kein Fallback-Text.
    if not response_msg:
        logger.debug("_dispatch_response: leere Antwort – nichts gesendet (FINISH)")
        return
    for prefix, handler in _RESPONSE_DISPATCH:
        if response_msg.startswith(prefix):
            await handler(response_msg=response_msg, bot=bot, chat_id=chat_id)
            return
    await asyncio.gather(
        update.message.reply_text(response_msg),
        speak_and_send(response_msg, bot, chat_id),
    )


# ---------------------------------------------------------------------------
# Private HITL-Handler
# ---------------------------------------------------------------------------

async def _handle_screenshot(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    analysis         = response_msg[len(Proto.SCREENSHOT):]
    screenshot_bytes = _screenshot_to_telegram_bytes()
    if screenshot_bytes:
        await bot.send_photo(chat_id=chat_id, photo=screenshot_bytes, caption=analysis)
    else:
        await bot.send_message(chat_id=chat_id, text=f"Screenshot-Analyse:\n{analysis}")
    # Phase 117 (Issue #45): Analyse in Memory schreiben damit chat_agent
    # bei Follow-up-Fragen den Screenshot-Kontext kennt.
    await _update_memory(chat_id, f"Screenshot erstellt und analysiert:\n{analysis}")
    # Phase 117 (Issue #45): Analyse in Memory schreiben damit chat_agent
    # bei Follow-up-Fragen den Screenshot-Kontext kennt.


async def _handle_confirm_computer(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts    = response_msg[len(Proto.CONFIRM_COMPUTER):].split(":", 3)
    action   = parts[0] if len(parts) > 0 else ""
    x        = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    y        = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    text_arg = parts[3] if len(parts) > 3 else ""
    display  = f"{action}: {text_arg}" if text_arg else f"{action} @ ({x}, {y})"
    confirmed = await request_confirmation(bot, chat_id, "computer_agent", display)
    if confirmed:
        if not check_action_rate_limit(chat_id, "destructive"):
            await bot.send_message(chat_id=chat_id, text="⚠️ Rate Limit: zu viele Aktionen – bitte kurz warten.")
            log_action("computer_agent", action, "action-rate-limited", chat_id, status="blocked")
            return
        output = computer_agent_execute(action, x, y, text_arg, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
        await _update_memory(chat_id, f"Desktop-Aktion ausgefuehrt: {display}\nErgebnis: {output}")
    else:
        log_action("computer_agent", action, "user rejected", chat_id, status="rejected")


async def _handle_confirm_terminal(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    command   = response_msg[len(Proto.CONFIRM_TERMINAL):]
    confirmed = await request_confirmation(bot, chat_id, "terminal_agent", command)
    if confirmed:
        if not check_action_rate_limit(chat_id, "destructive"):
            await bot.send_message(chat_id=chat_id, text="⚠️ Rate Limit: zu viele Aktionen – bitte kurz warten.")
            log_action("terminal_agent", command[:200], "action-rate-limited", chat_id, status="blocked")
            return
        output = terminal_agent_execute(command, chat_id)
        await bot.send_message(chat_id=chat_id, text=f"Output:\n\n{output}")
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
        await _update_memory(chat_id, f"Terminal-Befehl ausgefuehrt: {command}\nErgebnis: {output}")
    else:
        log_action("terminal_agent", command, "user rejected", chat_id, status="rejected")


async def _handle_confirm_create_event(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts      = response_msg[len(Proto.CONFIRM_CREATE_EVENT):].split("::")
    title      = parts[0] if len(parts) > 0 else ""
    start_time = parts[1] if len(parts) > 1 else ""
    end_time   = parts[2] if len(parts) > 2 else ""
    confirmed  = await request_confirmation(
        bot, chat_id, "calendar_agent",
        f"Neuer Termin: {title} am {start_time}"
    )
    if confirmed:
        output = calendar_event_create(title, start_time, end_time, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
        await _update_memory(chat_id, f"Kalendereintrag erstellt: {title} um {start_time}\nErgebnis: {output}")
    else:
        log_action("calendar_agent", "create_event", f"user rejected: {title}", chat_id, status="rejected")


async def _handle_confirm_file_write(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts        = response_msg[len(Proto.CONFIRM_FILE_WRITE):].split("::", 1)
    path_str     = parts[0]
    file_content = parts[1] if len(parts) > 1 else ""
    confirmed    = await request_confirmation(bot, chat_id, "file_agent", f"Schreibe nach: {path_str}")
    if confirmed:
        if not check_action_rate_limit(chat_id, "destructive"):
            await bot.send_message(chat_id=chat_id, text="⚠️ Rate Limit: zu viele Aktionen – bitte kurz warten.")
            log_action("file_agent", "write", f"action-rate-limited: {path_str}", chat_id, status="blocked")
            return
        output = file_agent_write(Path(path_str), file_content, chat_id)
        await bot.send_message(chat_id=chat_id, text=output)
        if len(output) <= _TTS_MAX_HITL_OUTPUT:
            await speak_and_send(output, bot, chat_id)
        await _update_memory(chat_id, f"Datei geschrieben: {path_str}\nErgebnis: {output}")
    else:
        log_action("file_agent", "write", f"user rejected: {path_str}", chat_id, status="rejected")


async def _handle_confirm_whatsapp(response_msg: str, bot: Bot, chat_id: int, **_) -> None:
    parts         = response_msg[len(Proto.CONFIRM_WHATSAPP):].split("::", 1)
    whatsapp_name = parts[0] if len(parts) > 0 else ""
    message_text  = parts[1] if len(parts) > 1 else ""
    confirmed = await request_confirmation(
        bot, chat_id, "whatsapp_agent",
        f"WhatsApp an {whatsapp_name}:\n{message_text}"
    )
    if confirmed:
        if not check_action_rate_limit(chat_id, "destructive"):
            await bot.send_message(chat_id=chat_id, text="⚠️ Rate Limit: zu viele Aktionen – bitte kurz warten.")
            log_action("whatsapp_agent", "send", f"action-rate-limited: {whatsapp_name}", chat_id, status="blocked")
            return
        success, detail = await send_whatsapp_message(whatsapp_name, message_text)
        await bot.send_message(chat_id=chat_id, text=detail)
        await _update_memory(chat_id, f"WhatsApp gesendet an {whatsapp_name}: {message_text}")
        log_action("whatsapp_agent", "send", f"to={whatsapp_name} len={len(message_text)}", chat_id,
                   status="executed" if success else "error")
    else:
        log_action("whatsapp_agent", "send", f"user rejected: {whatsapp_name}", chat_id, status="rejected")


_RESPONSE_DISPATCH: list[tuple[str, callable]] = [
    (Proto.SCREENSHOT,           _handle_screenshot),
    (Proto.CONFIRM_COMPUTER,     _handle_confirm_computer),
    (Proto.CONFIRM_TERMINAL,     _handle_confirm_terminal),
    (Proto.CONFIRM_CREATE_EVENT, _handle_confirm_create_event),
    (Proto.CONFIRM_FILE_WRITE,   _handle_confirm_file_write),
    (Proto.CONFIRM_WHATSAPP,     _handle_confirm_whatsapp),
]


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

@restricted
async def cmd_wa_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text(
            "Verwendung:\n"
            "/wa_contact add <Name> <WhatsApp-Name>\n"
            "/wa_contact remove <Name>\n"
            "/wa_contact list\n\n"
            'Beispiel:\n/wa_contact add Steffi "Steffi 🌞"'
        )
        return
    subcmd = ctx.args[0].lower()
    if subcmd == "list":
        await update.message.reply_text(list_whatsapp_contacts_formatted())
    elif subcmd == "add":
        if len(ctx.args) < 3:
            await update.message.reply_text('Verwendung: /wa_contact add <Name> <WhatsApp-Name>')
            return
        name = ctx.args[1]
        whatsapp_name = " ".join(ctx.args[2:])
        success, detail = await add_whatsapp_contact(name, whatsapp_name)
        await update.message.reply_text(detail)
    elif subcmd == "remove":
        if len(ctx.args) < 2:
            await update.message.reply_text("Verwendung: /wa_contact remove <Name>")
            return
        success, detail = await remove_whatsapp_contact(ctx.args[1])
        await update.message.reply_text(detail)
    else:
        await update.message.reply_text(f"Unbekannter Unterbefehl: {subcmd}\nVerfügbar: add, remove, list")


@restricted
async def cmd_wa_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id  = update.effective_chat.id
    thinking = await update.message.reply_text("Prüfe WhatsApp Status...")
    try:
        status = await get_service_status()
        if not status.get("ok"):
            await thinking.edit_text(
                "❌ WhatsApp Service nicht erreichbar.\n\n"
                "Stelle sicher dass der Service läuft:\n"
                "```\ncd whatsapp_service\nnpm install\n```"
            )
            return
        if status.get("ready"):
            await thinking.edit_text("✅ WhatsApp bereits verbunden – keine Anmeldung nötig.")
            return
        if status.get("qr_available"):
            qr_string = await get_qr_code()
            if qr_string:
                try:
                    import qrcode as qrcode_lib
                    import io
                    qr_img = qrcode_lib.make(qr_string)
                    buf    = io.BytesIO()
                    qr_img.save(buf, format="PNG")
                    buf.seek(0)
                    await thinking.delete()
                    await ctx.bot.send_photo(
                        chat_id=chat_id,
                        photo=buf.getvalue(),
                        caption=(
                            "📱 WhatsApp QR-Code scannen:\n\n"
                            "1. WhatsApp öffnen\n"
                            "2. Einstellungen → Verknüpfte Geräte\n"
                            "3. Gerät hinzufügen → QR scannen"
                        ),
                    )
                    return
                except ImportError:
                    await thinking.edit_text("QR-Code verfügbar, aber 'qrcode[pil]' fehlt.")
                    return
            await thinking.edit_text("QR-Code konnte nicht abgerufen werden.")
            return
        await thinking.edit_text("⏳ WhatsApp Service läuft, QR-Code wird generiert...\nBitte nochmal /wa_setup ausführen.")
    except Exception as e:
        logger.error(f"cmd_wa_setup Fehler: {e}", exc_info=True)
        with contextlib.suppress(Exception):
            await thinking.edit_text(f"Fehler: {e}")


@restricted
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Mac Agent bereit.\n\n"
        "Schick mir eine Nachricht oder Sprachnachricht, oder nutze:\n"
        "/ask <Frage> – Direkte Anfrage\n"
        "/clip <URL> – URL als Markdown-Notiz speichern\n"
        "/search <Begriff> – Notizen durchsuchen\n"
        "/remember <Text> – Persönliche Notiz speichern\n"
        "/reindex – Wissensbasis neu indexieren\n"
        "/tts on|off – Sprachausgabe aktivieren/deaktivieren\n"
        "/stop – Laufende Sprachausgabe stoppen\n"
        "/wa_setup – WhatsApp Web einrichten\n"
        "/wa_contact add/remove/list – WhatsApp-Kontakte verwalten\n"
        "/health – System Health Check\n"
        "/status – Agent Status\n"
        "/auditlog – Letzte Aktionen\n"
        "/mute_proactive [h] – Proaktive Nachrichten stumm (default 24h)"
    )


@restricted
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tts_status = "aktiviert" if is_tts_enabled() else "deaktiviert"
    wa_status  = "✅ verbunden" if is_session_ready() else "❌ nicht verbunden"
    try:
        from agent.retrieval import _get_collection
        col              = _get_collection()
        retrieval_status = f"aktiv ({col.count()} Chunks)" if col else "deaktiviert"
    except Exception:
        retrieval_status = "nicht verfügbar"
    await update.message.reply_text(
        f"Agent läuft.\nTTS: {tts_status}\nRetrieval: {retrieval_status}\nWhatsApp: {wa_status}"
    )


@restricted
async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.health_check import run_health_check
    await run_health_check(ctx.bot, update.effective_chat.id)


@restricted
async def cmd_mute_proactive(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from agent.proactive.heartbeat import mute_proactive, unmute_proactive, is_muted
    args = ctx.args or []
    if args and args[0].lower() == "off":
        unmute_proactive()
        await update.message.reply_text("Proaktive Nachrichten wieder aktiv.")
    else:
        hours = int(args[0]) if args and args[0].isdigit() else 24
        mute_proactive(hours)
        await update.message.reply_text(f"Proaktive Nachrichten für {hours}h stummgeschaltet. /mute_proactive off zum Reaktivieren.")


@restricted
async def cmd_auditlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log_path = Path.home() / ".fabbot" / "audit.log"
    if not log_path.exists():
        await update.message.reply_text("Noch keine Aktionen geloggt.")
        return
    last = deque(maxlen=10)
    with log_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.rstrip()
            if stripped:
                last.append(stripped)
    await update.message.reply_text("Letzte Aktionen:\n\n" + "\n".join(last))


@restricted
async def cmd_tts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
        status = "aktiviert" if is_tts_enabled() else "deaktiviert"
        await update.message.reply_text(f"Sprachausgabe ist aktuell {status}.\nVerwendung: /tts on oder /tts off")
        return
    enabled = ctx.args[0].lower() == "on"
    set_tts_enabled(enabled)
    await update.message.reply_text(f"Sprachausgabe {'aktiviert' if enabled else 'deaktiviert'}.")


@restricted
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if stop_speaking():
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
        await update.message.reply_text("Verwendung: /clip <URL>")
        return
    url      = ctx.args[0]
    chat_id  = update.effective_chat.id
    thinking = await update.message.reply_text(f"Lese {url} ...")
    result = await clip_agent(url, chat_id)
    if not result["ok"]:
        await thinking.edit_text(f"Fehler: {result['error']}")
        return
    await thinking.edit_text(
        f"Vorschau:\n\n{result['preview']}\n\nSpeichern als: {result['filename']}"
    )
    confirmed = await request_confirmation(ctx.bot, chat_id, "clip_agent", f"Speichern: {result['filename']}")
    if confirmed:
        output = clip_agent_write(result["path"], result["content"], chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=output)
        try:
            from agent.retrieval import index_file
            task = asyncio.create_task(index_file(result["path"]))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        except Exception as e:
            logger.debug(f"Retrieval index_file nach /clip fehlgeschlagen (ignoriert): {e}")
    else:
        log_action("clip_agent", "write", f"user rejected: {result['filename']}", chat_id, status="rejected")


@restricted
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    result = list_knowledge() if not ctx.args else search_knowledge(" ".join(ctx.args))
    await update.message.reply_text(result, parse_mode="Markdown")
    log_action("search", "search_knowledge", " ".join(ctx.args or []), chat_id, status="executed")


@restricted
async def cmd_remember(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args) if ctx.args else ""
    if not text:
        await update.message.reply_text("Verwendung: /remember <was ich mir merken soll>")
        return
    from agent.profile import add_note_to_profile
    success = await add_note_to_profile(text)
    if success:
        await update.message.reply_text(f"✅ Gemerkt: {text}")
    else:
        await update.message.reply_text("❌ Fehler beim Speichern der Notiz.")


@restricted
async def cmd_reindex(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    thinking = await update.message.reply_text("Indexiere Wissensbasis (force=True)...")
    try:
        from agent.retrieval import index_all, _get_collection
        await index_all(force=True)
        col   = _get_collection()
        count = col.count() if col else 0
        await thinking.edit_text(f"✅ Wissensbasis neu indexiert – {count} Chunks gesamt.")
    except ImportError:
        await thinking.edit_text("❌ chromadb nicht installiert – Retrieval nicht verfügbar.")
    except Exception as e:
        logger.error(f"cmd_reindex Fehler: {e}")
        await thinking.edit_text(f"❌ Fehler bei Re-Indexierung: {e}")


# ---------------------------------------------------------------------------
# Media Handlers
# ---------------------------------------------------------------------------

@restricted
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _is_duplicate(update):
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    caption = update.message.caption or ""
    if caption:
        is_safe, result = await sanitize_input_async(caption, user_id)
        if not is_safe:
            log_blocked(result, caption, user_id)
            await update.message.reply_text(f"Eingabe abgelehnt: {result}")
            return
        caption = result
    thinking = await update.message.reply_text("Analysiere Bild...")
    try:
        import base64
        photo     = update.message.photo[-1]
        tg_file   = await ctx.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        resized, media_type = _resize_image(bytes(img_bytes), "image/jpeg")
        img_b64   = base64.standard_b64encode(resized).decode("utf-8")
        vision_result = await analyze_image_direct(img_b64, caption, media_type, chat_id)
        await update.message.reply_text(vision_result)
        await speak_and_send(vision_result, ctx.bot, chat_id)
        await _update_vision_memory(chat_id, caption, vision_result)
    except Exception as e:
        logger.error(f"on_photo Fehler: {e}", exc_info=True)
        await update.message.reply_text("Fehler bei der Bildanalyse.")
    finally:
        await _delete_thinking(thinking)


@restricted
async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _is_duplicate(update):
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    doc     = update.message.document
    if not doc or not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Nur Bilder werden unterstützt (JPEG, PNG, WebP).")
        return
    if doc.file_size and doc.file_size > _IMAGE_MAX_BYTES:
        await update.message.reply_text(f"Bild zu groß (max. {_IMAGE_MAX_BYTES // 1_000_000} MB).")
        return
    caption = update.message.caption or ""
    if caption:
        is_safe, result = await sanitize_input_async(caption, user_id)
        if not is_safe:
            log_blocked(result, caption, user_id)
            await update.message.reply_text(f"Eingabe abgelehnt: {result}")
            return
        caption = result
    thinking = await update.message.reply_text("Analysiere Bild...")
    try:
        import base64
        tg_file   = await ctx.bot.get_file(doc.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        resized, media_type = _resize_image(bytes(img_bytes), doc.mime_type or "image/jpeg")
        img_b64 = base64.standard_b64encode(resized).decode("utf-8")
        vision_result = await analyze_image_direct(img_b64, caption, media_type, chat_id)
        await update.message.reply_text(vision_result)
        await speak_and_send(vision_result, ctx.bot, chat_id)
        await _update_vision_memory(chat_id, caption, vision_result)
    except Exception as e:
        logger.error(f"on_document Fehler: {e}", exc_info=True)
        await update.message.reply_text("Fehler bei der Bildanalyse.")
    finally:
        await _delete_thinking(thinking)


@restricted
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _is_duplicate(update):
        return
    await handle_message_text(update, ctx.bot, update.message.text)


@restricted
async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if await _is_duplicate(update):
        return
    thinking = await update.message.reply_text("Transkribiere...")
    try:
        voice       = update.message.voice
        tg_file     = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await tg_file.download_as_bytearray()
        text        = await transcribe_audio(bytes(audio_bytes))
        if not text:
            await update.message.reply_text("Transkription fehlgeschlagen.")
            return
        await thinking.edit_text(f"_{text}_", parse_mode="Markdown")
        thinking = None
        await handle_message_text(update, ctx.bot, text)
    except (TimedOut, NetworkError) as e:
        logger.warning(f"Telegram network error in voice handler: {e}")
        await update.message.reply_text("Netzwerkfehler – bitte nochmal versuchen.")
    except Exception as e:
        logger.error(f"Voice handler error: {e}", exc_info=True)
        await update.message.reply_text("Fehler bei der Verarbeitung der Sprachnachricht.")
    finally:
        if thinking is not None:
            await _delete_thinking(thinking)


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------

async def handle_message_text(update: Update, bot: Bot, text: str) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_safe, clean_text = await _sanitize_and_validate(text, user_id, update)
    if not is_safe:
        return

    thinking = await update.message.reply_text("Denke nach...")
    try:
        state = {
            "messages":         [HumanMessage(content=clean_text)],
            "telegram_chat_id": chat_id,
            "next_agent":       None,
        }
        config = {"configurable": {"thread_id": str(chat_id)}, "recursion_limit": 10}
        # Phase 104 (Issue #16b): Lock verhindert concurrent ainvoke() auf denselben
        # thread_id – bei schnellem Chatten würden sonst zwei Handler gleichzeitig
        # den LangGraph SQLite-Checkpointer beschreiben → Race Condition → Duplikate.
        async with _get_invoke_lock(chat_id):
            response_msg = await _invoke_and_extract(state, config)
        await _delete_thinking(thinking)
        await _dispatch_response(response_msg, bot, chat_id, update)
        if response_msg:
            from agent.proactive.collector import collect_entities
            asyncio.create_task(collect_entities(
                user_message=clean_text,
                bot_response=response_msg,
            ))

    except RateLimitError:
        await _delete_thinking(thinking)
        await update.message.reply_text("Zu viele Anfragen – bitte kurz warten.")
    except APIStatusError as e:
        await _delete_thinking(thinking)
        if e.status_code == 529:
            await update.message.reply_text("Anthropic ist gerade überlastet – bitte in 1-2 Minuten nochmal versuchen.")
        else:
            await update.message.reply_text(f"API Fehler ({e.status_code}) – bitte Administrator informieren.")
    except APIConnectionError:
        await _delete_thinking(thinking)
        await update.message.reply_text("Verbindungsfehler zur KI – bitte nochmal versuchen.")
    except (TimedOut, NetworkError):
        await _delete_thinking(thinking)
        await update.message.reply_text("Netzwerkfehler – bitte nochmal versuchen.")
    except RetryAfter as e:
        await _delete_thinking(thinking)
        await update.message.reply_text(f"Telegram meldet: bitte {e.retry_after}s warten.")
    except asyncio.TimeoutError:
        await _delete_thinking(thinking)
        await update.message.reply_text("Timeout – bitte nochmal versuchen.")
    except GraphRecursionError:
        await _delete_thinking(thinking)
        await update.message.reply_text("Anfrage zu komplex – bitte anders formulieren.")
    except Exception as e:
        logger.error(f"Unexpected agent error: {e}", exc_info=True)
        await _delete_thinking(thinking)
        await update.message.reply_text("Ein unerwarteter Fehler ist aufgetreten.")


# ---------------------------------------------------------------------------
# Lifecycle Hooks
# ---------------------------------------------------------------------------

async def _post_init(app: Application) -> None:
    from agent.supervisor import init_graph, cleanup_checkpoints
    await init_graph()
    logger.info("SqliteSaver-Checkpointer initialisiert.")
    await cleanup_checkpoints(max_per_thread=200)

    # Phase 92: Audit-Logger erst jetzt initialisieren (nach logging.basicConfig()).
    from agent.audit import setup_audit_logger
    setup_audit_logger()
    logger.info("Audit-Logger initialisiert.")

    # Phase 95 (Issue #6): Harte Model-Validierung beim Start.
    from agent.llm import validate_models_on_startup
    validate_models_on_startup()
    logger.info("LLM Model-Validierung abgeschlossen.")

    from agent.telemetry import setup_telemetry
    setup_telemetry()

    # Whisper-Modell vorladen – verzögert damit ChromaDB-Init abgeschlossen ist.
    async def _warmup_whisper_delayed():
        await asyncio.sleep(10)
        try:
            loop = asyncio.get_event_loop()
            from bot.transcribe import _get_model
            await loop.run_in_executor(None, _get_model)
            logger.info("Whisper-Modell vorgeladen.")
        except Exception as e:
            logger.warning(f"Whisper-Warmup fehlgeschlagen (ignoriert): {e}")
    asyncio.create_task(_warmup_whisper_delayed())

    async def _warmup_profile():
        try:
            loop = asyncio.get_running_loop()
            from agent.profile import load_profile
            await asyncio.wait_for(loop.run_in_executor(None, load_profile), timeout=5.0)
            logger.info("Profil vorgeladen (Keychain-Zugriff abgeschlossen).")
        except asyncio.TimeoutError:
            logger.warning("Profil-Warmup Timeout (5s) – Keychain möglicherweise gesperrt. Profil wird beim ersten Aufruf erneut versucht.")
        except Exception as e:
            logger.warning(f"Profil-Warmup fehlgeschlagen (ignoriert): {e}")
    asyncio.create_task(_warmup_profile())

    chat_id_str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id_str:
        fallback_raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        chat_id_str  = fallback_raw.split(",")[0].strip() if fallback_raw else ""

    if not chat_id_str:
        logger.critical("Weder TELEGRAM_CHAT_ID noch TELEGRAM_ALLOWED_USER_IDS gesetzt – Scheduler nicht gestartet.")
        return

    try:
        chat_id = int(chat_id_str)
    except ValueError as e:
        logger.critical(f"Chat-ID '{chat_id_str}' ist ungültig – Scheduler nicht gestartet. Fehler: {e}")
        return

    try:
        wa_started = await start_service()
        logger.info("WhatsApp Service gestartet." if wa_started else "WhatsApp Service nicht verfügbar.")
    except Exception as e:
        logger.warning(f"WhatsApp Service Start übersprungen: {e}")

    from bot.briefing import run_briefing_scheduler
    task_briefing = asyncio.create_task(run_briefing_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_briefing)
    task_briefing.add_done_callback(
        lambda t: logger.error(f"Briefing Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    from bot.reminders import run_reminder_scheduler
    task_reminders = asyncio.create_task(run_reminder_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_reminders)
    task_reminders.add_done_callback(
        lambda t: logger.error(f"Reminders Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    from bot.heartbeat_scheduler import run_heartbeat_scheduler
    task_heartbeat = asyncio.create_task(run_heartbeat_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_heartbeat)
    task_heartbeat.add_done_callback(
        lambda t: logger.error(f"Heartbeat Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    from bot.health_check import run_health_check_scheduler
    task_health = asyncio.create_task(run_health_check_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_health)
    task_health.add_done_callback(
        lambda t: logger.error(f"Health-Check Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    from bot.party_report import run_party_report_scheduler
    task_party = asyncio.create_task(run_party_report_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_party)
    task_party.add_done_callback(
        lambda t: logger.error(f"Party-Report Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    from bot.session_summary import run_session_summary_scheduler
    task_summary = asyncio.create_task(run_session_summary_scheduler(app.bot, chat_id))
    _scheduler_tasks.append(task_summary)
    task_summary.add_done_callback(
        lambda t: logger.error(f"Session-Summary Scheduler unerwartet beendet: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )

    try:
        from agent.retrieval import index_all
        task_retrieval = asyncio.create_task(index_all())
        task_retrieval.add_done_callback(
            lambda t: logger.error(f"Retrieval Index-Aufbau fehlgeschlagen: {t.exception()}")
            if not t.cancelled() and t.exception() else None
        )
        logger.info("Retrieval Index-Aufbau gestartet (Background).")
    except ImportError:
        logger.info("chromadb nicht installiert – Retrieval deaktiviert.")
    except Exception as e:
        logger.warning(f"Retrieval Index-Aufbau fehlgeschlagen (ignoriert): {e}")


async def _post_shutdown(app: Application) -> None:
    for task in _scheduler_tasks:
        if not task.done():
            task.cancel()
    try:
        await stop_service()
    except Exception as e:
        logger.warning(f"WhatsApp Service Stop fehlgeschlagen (ignoriert): {e}")
    from agent.supervisor import close_graph
    await close_graph()
    logger.info("SqliteSaver-Verbindung geschlossen.")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

def build_bot() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nicht gesetzt")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(5)
        .build()
    )
    app.add_handler(CommandHandler("health",          cmd_health,          block=False))
    app.add_handler(CommandHandler("mute_proactive",  cmd_mute_proactive,  block=False))
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("auditlog",   cmd_auditlog))
    app.add_handler(CommandHandler("tts",        cmd_tts))
    app.add_handler(CommandHandler("stop",       cmd_stop))
    app.add_handler(CommandHandler("ask",        cmd_ask,        block=False))
    app.add_handler(CommandHandler("clip",       cmd_clip,       block=False))
    app.add_handler(CommandHandler("search",     cmd_search,     block=False))
    app.add_handler(CommandHandler("remember",   cmd_remember,   block=False))
    app.add_handler(CommandHandler("reindex",    cmd_reindex,    block=False))
    app.add_handler(CommandHandler("wa_setup",   cmd_wa_setup,   block=False))
    app.add_handler(CommandHandler("wa_contact", cmd_wa_contact, block=False))
    app.add_handler(MessageHandler(filters.VOICE,          on_voice,    block=False))
    app.add_handler(MessageHandler(filters.PHOTO,          on_photo,    block=False))
    app.add_handler(MessageHandler(filters.Document.IMAGE, on_document, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message, block=False))
    register_confirmation_handler(app)
    return app
