"""
bot/flug/app.py – Phase 247 (Issue #385)

Lebenszyklus des Flug-Bots als zweite PTB-Application im FabBot-Prozess.

Isolation:
- eigener Token, eigene Allowlist (FLUG_ALLOWED_USER_IDS)
- eine Schranke in Gruppe -1 läuft vor allen Handlern und stoppt jedes
  Update fremder Nutzer und jedes Update außerhalb privater Chats per
  ApplicationHandlerStop – auch dann, wenn sie selbst einen Fehler wirft
  (fail-closed); der Error-Handler stoppt die Verarbeitung ebenfalls
- der Flug-Bot kennt nur seine eigenen Handler; Supervisor, Terminal- und
  File-Agent sind von hier aus nicht erreichbar

Stabilität: Der Start läuft als Hintergrund-Task und wirft nie – ohne Token,
bei Netzproblemen oder Startfehlern läuft FabBot unverändert weiter.
Exception-Texte werden nicht geloggt, weil PTB den Token in
InvalidToken-Meldungen zitiert.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, ApplicationBuilder, ApplicationHandlerStop, ContextTypes, TypeHandler

from agent.config import get_settings
from bot.flug.dialog import build_handlers
from bot.flug.drossel import Drossel
from bot.flug.watcher import run_flug_watch_scheduler

logger = logging.getLogger(__name__)

_ERLAUBTE_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY]


@dataclass(frozen=True)
class _Laufend:
    app: Any
    task: asyncio.Task | None


_laufend: _Laufend | None = None
_start_task: asyncio.Task | None = None


def parse_ids(roh: str) -> frozenset[int]:
    """Kommagetrennte User-IDs. Fail-closed: ein ungültiger Eintrag → leere Menge."""
    teile = [t.strip() for t in (roh or "").split(",") if t.strip()]
    try:
        return frozenset(int(t) for t in teile)
    except ValueError:
        return frozenset()


# ── Schranke ──────────────────────────────────────────────────────────────────


def _ist_erlaubt(update: Update, erlaubt: frozenset[int]) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return user is not None and chat is not None and chat.type == ChatType.PRIVATE and user.id in erlaubt


async def _weise_ab(update: Update, erlaubt: frozenset[int], abweisungen: Drossel) -> None:
    user, nachricht = update.effective_user, update.effective_message
    if user is None or nachricht is None:
        return
    if not abweisungen.erlaube(user.id):
        logger.debug("Flug-Bot: wiederholter Zugriffsversuch ignoriert")
        return
    if user.id in erlaubt:
        await nachricht.reply_text("Ich funktioniere nur im privaten Chat.")
        return
    logger.warning(f"Flug-Bot: Zugriff verweigert für User-ID {user.id}")
    await nachricht.reply_text(
        f"⛔ Kein Zugriff.\nDeine Telegram-ID: {user.id}\n"
        "Schick sie dem Betreiber dieses Bots, damit du freigeschaltet wirst."
    )


def erstelle_schranke(erlaubt: frozenset[int]):
    """Handler für Gruppe -1: lässt nur freigeschaltete Nutzer im Privatchat durch."""
    abweisungen = Drossel(max_anzahl=1, fenster_sekunden=3600)

    async def schranke(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if _ist_erlaubt(update, erlaubt):
                return
            await _weise_ab(update, erlaubt, abweisungen)
        except Exception as e:
            logger.error(f"Flug-Bot: Schranke fehlgeschlagen ({type(e).__name__}) – Update verworfen")
        raise ApplicationHandlerStop

    return schranke


async def _fehler_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Flug-Bot: Fehler im Handler ({type(ctx.error).__name__})")
    # Ohne das liefe PTB nach einem Fehler in Gruppe -1 mit Gruppe 0 weiter.
    raise ApplicationHandlerStop


def build_flug_app(token: str, erlaubt: frozenset[int]) -> Application:
    app = (
        ApplicationBuilder().token(token).read_timeout(30).write_timeout(30).connect_timeout(30).pool_timeout(5).build()
    )
    app.add_handler(TypeHandler(Update, erstelle_schranke(erlaubt)), group=-1)
    app.add_handlers(build_handlers())
    app.add_error_handler(_fehler_handler)
    return app


# ── Lebenszyklus ──────────────────────────────────────────────────────────────


def _task_beendet(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error(f"Flug-Watch Scheduler unerwartet beendet ({type(task.exception()).__name__})")


async def _aufraeumen(app: Any) -> None:
    if app is None:
        return
    for schritt in (app.updater.stop, app.stop, app.shutdown):
        try:
            await schritt()
        except Exception as e:
            logger.debug(f"Flug-Bot: Stop-Schritt übersprungen ({type(e).__name__})")


async def _benachrichtige(bot: Any, chat_id: str | int, text: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error(f"Flug-Bot: Hinweis an FabBot-Chat nicht zustellbar ({type(e).__name__})")


def _konfiguration() -> tuple[str, frozenset[int], str] | None:
    """(Bot-Token, Allowlist, Travelpayouts-Token) oder None, wenn der Flug-Bot aus bleibt."""
    cfg = get_settings()
    token = cfg.flug_bot_token.get_secret_value().strip()
    if not token:
        logger.info("FLUG_BOT_TOKEN nicht gesetzt – Flug-Bot deaktiviert.")
        return None
    erlaubt = parse_ids(cfg.flug_allowed_user_ids)
    if not erlaubt:
        logger.warning("FLUG_ALLOWED_USER_IDS leer oder ungültig – Flug-Bot deaktiviert.")
        return None
    if token == cfg.telegram_bot_token.get_secret_value().strip():
        logger.error("FLUG_BOT_TOKEN ist identisch mit TELEGRAM_BOT_TOKEN – Flug-Bot deaktiviert.")
        return None
    return token, erlaubt, cfg.travelpayouts_token.get_secret_value().strip()


async def starte_flug_bot(fehler_bot: Any, fehler_chat_id: str | int) -> bool:
    """Startet den Flug-Bot, falls konfiguriert. Wirft nie (außer Cancellation); True = läuft."""
    global _laufend
    konfiguration = _konfiguration()
    if konfiguration is None:
        return False
    token, erlaubt, tp_token = konfiguration

    app = None
    try:
        app = build_flug_app(token, erlaubt)
        await app.initialize()
        await app.start()
        if app.updater is None:
            raise RuntimeError("Flug-Bot ohne Updater gebaut")
        await app.updater.start_polling(drop_pending_updates=True, allowed_updates=_ERLAUBTE_UPDATES)
    except asyncio.CancelledError:
        await _aufraeumen(app)
        raise
    except Exception as e:
        logger.error(f"Flug-Bot: Start fehlgeschlagen ({type(e).__name__})")
        await _aufraeumen(app)
        await _benachrichtige(
            fehler_bot,
            fehler_chat_id,
            f"⚠️ Flug-Bot konnte nicht starten ({type(e).__name__}). FabBot läuft normal weiter.",
        )
        return False

    task = None
    if tp_token:
        task = asyncio.create_task(
            run_flug_watch_scheduler(app.bot, tp_token, erlaubt, fehler_bot, fehler_chat_id),
            name="scheduler:flug_watch",
        )
        task.add_done_callback(_task_beendet)
    else:
        logger.info("TRAVELPAYOUTS_TOKEN nicht gesetzt – Flug-Watch deaktiviert.")

    _laufend = _Laufend(app=app, task=task)
    logger.info("Flug-Bot gestartet")
    return True


def starte_im_hintergrund(fehler_bot: Any, fehler_chat_id: str | int) -> asyncio.Task:
    """Start ohne FabBots eigenen Start aufzuhalten (get_me & Co. können bei Netzproblemen hängen)."""
    global _start_task
    _start_task = asyncio.create_task(starte_flug_bot(fehler_bot, fehler_chat_id), name="flug_bot:start")
    return _start_task


async def stoppe_flug_bot() -> None:
    """Bricht einen laufenden Start ab, stoppt Scheduler und Polling. No-op, wenn nichts läuft."""
    global _laufend, _start_task
    start, _start_task = _start_task, None
    if start is not None and not start.done():
        start.cancel()
        await asyncio.wait({start})

    laufend, _laufend = _laufend, None
    if laufend is None:
        return
    if laufend.task is not None and not laufend.task.done():
        laufend.task.cancel()
        await asyncio.wait({laufend.task})
    await _aufraeumen(laufend.app)
    logger.info("Flug-Bot gestoppt")
