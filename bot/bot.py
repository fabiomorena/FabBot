import logging
import os
import asyncio
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TimedOut, NetworkError, RetryAfter
from langchain_core.messages import HumanMessage, AIMessage
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
from agent.agents.vision_agent import analyze_image_direct

logger = logging.getLogger(__name__)

_TTS_MAX_HITL_OUTPUT = 300

_IMAGE_MAX_PX = 1920
_IMAGE_MAX_BYTES = 5_000_000  # 5MB


def _resize_image(img_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Skaliert Bild auf max. 1920px falls nötig. Format bleibt erhalten."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        if max(img.width, img.height) <= _IMAGE_MAX_PX:
            logger.debug(f"Bild {img.width}x{img.height} – kein Resize nötig")
            return img_bytes, mime_type
        img.thumbnail((_IMAGE_MAX_PX, _IMAGE_MAX_PX), Image.LANCZOS)
        output = io.BytesIO()
        fmt = "PNG" if mime_type == "image/png" else "JPEG"
        save_kwargs = {"optimize": True}
        if fmt == "JPEG":
            save_kwargs["quality"] = 90
        if fmt == "PNG" and img.mode == "RGBA":
            pass
        elif img.mode in ("RGBA", "P") and fmt == "JPEG":
            img = img.convert("RGB")
        img.save(output, format=fmt, **save_kwargs)
        result = output.getvalue()
        logger.info(f"Bild skaliert: {img.width}x{img.height}px, {len(img_bytes)}b → {len(result)}b")
        return result, mime_type
    except Exception as e:
        logger.warning(f"Bild-Resize fehlgeschlagen (Original wird verwendet): {e}")
        return img_bytes, mime_type


_scheduler_tasks: list = []

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 2.0


def _extract_content(msg) -> str:
    """Extrahiert Text aus einer LangChain Message."""
    content = msg.content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        ).strip()
    return str(content).strip()


async def _update_memory(chat_id: int, result_text: str) -> None:
    """Schreibt das HITL-Ergebnis als AIMessage in den LangGraph State."""
    try:
        from agent.supervisor import agent_graph
        config = {"configurable": {"thread_id": str(chat_id)}}
        await agent_graph.aupdate_state(
            config,
            {"messages": [AIMessage(content=f"__MEMORY__:{result_text}")]},
        )
    except Exception as e:
        logger.warning(f"Memory update nach HITL fehlgeschlagen (nicht kritisch): {e}")


async def _update_vision_memory(chat_id: int, caption: str, result: str) -> None:
    """Schreibt Bildanalyse als sichtbare HumanMessage+AIMessage in den State."""
    try:
        from agent.supervisor import agent_graph
        from langchain_core.messages import HumanMessage as HM
        config = {"configurable": {"thread_id": str(chat_id)}}
        human_text = f"[Foto] {caption}" if caption else "[Foto gesendet]"
        await agent_graph.aupdate_state(
            config,
            {"messages": [HM(content=human_text), AIMessage(content=result)]},
            as_node="supervisor",
        )
        logger.info(f"Vision memory gespeichert: {result[:80]}")
    except Exception as e:
        logger.warning(f"Vision memory update fehlgeschlagen: {e}", exc_info=True)


async def _invoke_with_retry(state: dict, config: dict) -> dict:
    """Ruft agent_graph.ainvoke mit exponentiellem Backoff bei 529-Fehlern auf."""
    from agent.supervisor import agent_graph

    last_exception = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return await agent_graph.ainvoke(state, config=config)
        except APIStatusError as e:
            if e.status_code == 529:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Anthropic 529 Overloaded – Versuch {attempt + 1}/{_RETRY_MAX_ATTEMPTS}, "
                    f"warte {delay:.0f}s..."
                )
                last_exception = e
                if attempt < _RETRY_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(delay)
            else:
                raise
    raise last_exception


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
        await _update_memory(chat_id, f"Desktop-Aktion ausgefuehrt: {display}\nErgebnis: {output}")
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
        await _update_memory(chat_id, f"Terminal-Befehl ausgefuehrt: {command}\nErgebnis: {output}")
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
        await _update_memory(chat_id, f"Kalendereintrag erstellt: {title} um {start_time}\nErgebnis: {output}")
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
        await _update_memory(chat_id, f"Datei geschrieben: {path_str}\nErgebnis: {output}")
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
        "/remember <Text> – Persönliche Notiz speichern\n"
        "/reindex – Wissensbasis neu indexieren\n"
        "/tts on|off – Sprachausgabe aktivieren/deaktivieren\n"
        "/stop – Laufende Sprachausgabe stoppen\n"
        "/status – Agent Status\n"
        "/auditlog – Letzte Aktionen"
    )


@restricted
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tts_status = "aktiviert" if is_tts_enabled() else "deaktiviert"
    # Phase 77: Retrieval-Status anzeigen
    try:
        from agent.retrieval import _get_collection
        col = _get_collection()
        retrieval_status = f"aktiv ({col.count()} Chunks)" if col else "deaktiviert"
    except Exception:
        retrieval_status = "nicht verfügbar"
    await update.message.reply_text(
        f"Agent läuft.\nTTS: {tts_status}\nRetrieval: {retrieval_status}"
    )


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
        # Phase 77: Neue Notiz sofort in Retrieval-Index aufnehmen
        try:
            from agent.retrieval import index_file
            asyncio.create_task(index_file(result["path"]))
            logger.info(f"Retrieval: Neue Notiz '{result['filename']}' wird indexiert.")
        except Exception as e:
            logger.debug(f"Retrieval index_file nach /clip fehlgeschlagen (ignoriert): {e}")
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
async def cmd_remember(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args) if ctx.args else ""
    if not text:
        await update.message.reply_text(
            "Verwendung: /remember <was ich mir merken soll>\n"
            "Beispiel: /remember ich arbeite gerade auch an Projekt X"
        )
        return
    from agent.profile import add_note_to_profile
    success = await add_note_to_profile(text)
    if success:
        await update.message.reply_text(f"✅ Gemerkt: {text}")
    else:
        await update.message.reply_text("❌ Fehler beim Speichern der Notiz.")


@restricted
async def cmd_reindex(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Phase 77: Löst eine vollständige Neu-Indexierung der Wissensbasis aus.
    force=True: alle Dateien werden re-embedded, auch unveränderte.
    """
    thinking = await update.message.reply_text("Indexiere Wissensbasis (force=True)...")
    try:
        from agent.retrieval import index_all, _get_collection
        await index_all(force=True)
        col = _get_collection()
        count = col.count() if col else 0
        await thinking.edit_text(f"✅ Wissensbasis neu indexiert – {count} Chunks gesamt.")
    except ImportError:
        await thinking.edit_text("❌ chromadb nicht installiert – Retrieval nicht verfügbar.")
    except Exception as e:
        logger.error(f"cmd_reindex Fehler: {e}")
        await thinking.edit_text(f"❌ Fehler bei Re-Indexierung: {e}")


@restricted
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
        photo = update.message.photo[-1]
        tg_file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        resized, media_type = _resize_image(bytes(img_bytes), "image/jpeg")
        img_b64 = base64.standard_b64encode(resized).decode("utf-8")

        vision_result = await analyze_image_direct(img_b64, caption, media_type, chat_id)

        await thinking.delete()
        await update.message.reply_text(vision_result)
        await speak_and_send(vision_result, ctx.bot, chat_id)
        await _update_vision_memory(chat_id, caption, vision_result)

    except Exception as e:
        logger.error(f"on_photo Fehler: {e}", exc_info=True)
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Fehler bei der Bildanalyse.")


@restricted
async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    doc = update.message.document

    if not doc or not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Nur Bilder werden unterstützt (JPEG, PNG, WebP).")
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
        tg_file = await ctx.bot.get_file(doc.file_id)
        img_bytes = await tg_file.download_as_bytearray()
        logger.info(f"on_document: {len(img_bytes)} bytes, mime={doc.mime_type}, file_id={doc.file_id[:20]}")
        img_b64 = base64.standard_b64encode(bytes(img_bytes)).decode("utf-8")
        media_type = doc.mime_type or "image/jpeg"
        vision_result = await analyze_image_direct(img_b64, caption, media_type, chat_id)
        logger.info(f"VISION RESULT: {vision_result[:100]}")
        await thinking.delete()
        await update.message.reply_text(vision_result)
        await speak_and_send(vision_result, ctx.bot, chat_id)
        await _update_vision_memory(chat_id, caption, vision_result)

    except Exception as e:
        logger.error(f"on_document Fehler: {e}", exc_info=True)
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Fehler bei der Bildanalyse.")


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
        config = {"configurable": {"thread_id": str(chat_id)}, "recursion_limit": 10}

        result_state = await _invoke_with_retry(state, config)

        input_count = len(state["messages"])
        new_messages = result_state["messages"][input_count:]
        ai_messages = [m for m in new_messages if isinstance(m, AIMessage)]
        if not ai_messages:
            ai_messages = [m for m in result_state["messages"] if isinstance(m, AIMessage)]
        response_msg = _extract_content(ai_messages[-1]) if ai_messages else "Keine Antwort vom Agent."

        if len(ai_messages) >= 2:
            prev_content = _extract_content(ai_messages[-2])
            if response_msg == prev_content and response_msg:
                logger.warning("bot.py: Dedup-Sicherheitsnetz – Wiederholung abgefangen.")
                response_msg = "Noch etwas?"

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

    except APIStatusError as e:
        if e.status_code == 529:
            logger.error(f"Anthropic 529 Overloaded – alle {_RETRY_MAX_ATTEMPTS} Versuche fehlgeschlagen")
            try:
                await thinking.delete()
            except Exception:
                pass
            await update.message.reply_text(
                "Anthropic ist gerade überlastet – bitte in 1-2 Minuten nochmal versuchen."
            )
        else:
            logger.error(f"Anthropic API status error: {e.status_code} {e.message}")
            try:
                await thinking.delete()
            except Exception:
                pass
            await update.message.reply_text(f"API Fehler ({e.status_code}) – bitte Administrator informieren.")

    except APIConnectionError as e:
        logger.warning(f"Anthropic connection error: {e}")
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text("Verbindungsfehler zur KI – bitte nochmal versuchen.")

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
    """Initialisiert alle Background-Tasks nachdem der Event Loop gestartet ist."""
    from agent.supervisor import init_graph
    await init_graph()
    logger.info("SqliteSaver-Checkpointer initialisiert.")
    allowed_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    if allowed_ids:
        chat_id = int(allowed_ids.split(",")[0].strip())

        from bot.briefing import run_briefing_scheduler
        task_briefing = asyncio.create_task(run_briefing_scheduler(app.bot, chat_id))
        _scheduler_tasks.append(task_briefing)
        task_briefing.add_done_callback(
            lambda t: logger.error(f"Briefing Scheduler unerwartet beendet: {t.exception()}")
            if not t.cancelled() and t.exception() else None
        )
        logger.info("Morning Briefing Scheduler gestartet.")

        from bot.reminders import run_reminder_scheduler
        task_reminders = asyncio.create_task(run_reminder_scheduler(app.bot, chat_id))
        _scheduler_tasks.append(task_reminders)
        task_reminders.add_done_callback(
            lambda t: logger.error(f"Reminder Scheduler unerwartet beendet: {t.exception()}")
            if not t.cancelled() and t.exception() else None
        )
        logger.info("Reminder Scheduler gestartet.")

        from bot.health_check import run_health_check_scheduler
        task_health = asyncio.create_task(run_health_check_scheduler(app.bot, chat_id))
        _scheduler_tasks.append(task_health)
        task_health.add_done_callback(
            lambda t: logger.error(f"Health Check Scheduler unerwartet beendet: {t.exception()}")
            if not t.cancelled() and t.exception() else None
        )
        logger.info("Health Check Scheduler gestartet.")

        from bot.party_report import run_party_report_scheduler
        task_party = asyncio.create_task(run_party_report_scheduler(app.bot, chat_id))
        _scheduler_tasks.append(task_party)
        task_party.add_done_callback(
            lambda t: logger.error(f"Party Report Scheduler unerwartet beendet: {t.exception()}")
            if not t.cancelled() and t.exception() else None
        )
        logger.info("Party Report Scheduler gestartet.")

        # Phase 77: Retrieval-Index beim Start aufbauen (Background-Task)
        # Fail-safe: chromadb nicht installiert → silently skipped
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
    """Schliesst alle Ressourcen sauber beim Shutdown."""
    for task in _scheduler_tasks:
        if not task.done():
            task.cancel()
    if _scheduler_tasks:
        logger.info(f"{len(_scheduler_tasks)} Scheduler-Tasks abgebrochen.")
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
    app.add_handler(CommandHandler("remember", cmd_remember, block=False))
    app.add_handler(CommandHandler("reindex", cmd_reindex, block=False))
    app.add_handler(MessageHandler(filters.VOICE, on_voice, block=False))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo, block=False))
    app.add_handler(MessageHandler(filters.Document.IMAGE, on_document, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message, block=False))
    register_confirmation_handler(app)
    return app
