"""
Reminder-System fuer FabBot.
Speichert Erinnerungen in SQLite und sendet sie proaktiv per Telegram.
"""
import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".fabbot" / "reminders.db"


def _init_db() -> None:
    """Erstellt die Reminder-Tabelle falls nicht vorhanden."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


def add_reminder(chat_id: int, text: str, remind_at: datetime) -> int:
    """Speichert eine neue Erinnerung. Gibt die ID zurück."""
    _init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (chat_id, text, remind_at) VALUES (?, ?, ?)",
            (chat_id, text, remind_at.isoformat())
        )
        conn.commit()
        return cursor.lastrowid


def get_pending_reminders() -> list[dict]:
    """Gibt alle fälligen, noch nicht gesendeten Erinnerungen zurück."""
    _init_db()
    now = datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, text, remind_at FROM reminders WHERE sent=0 AND remind_at <= ?",
            (now,)
        ).fetchall()
    return [{"id": r[0], "chat_id": r[1], "text": r[2], "remind_at": r[3]} for r in rows]


def list_reminders(chat_id: int) -> list[dict]:
    """Listet alle offenen Erinnerungen fuer einen Chat."""
    _init_db()
    now = datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, text, remind_at FROM reminders WHERE chat_id=? AND sent=0 AND remind_at > ? ORDER BY remind_at",
            (chat_id, now)
        ).fetchall()
    return [{"id": r[0], "text": r[1], "remind_at": r[2]} for r in rows]


def mark_sent(reminder_id: int) -> None:
    """Markiert eine Erinnerung als gesendet."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, chat_id: int) -> bool:
    """Löscht eine Erinnerung. Gibt True zurück wenn erfolgreich."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id=? AND chat_id=? AND sent=0",
            (reminder_id, chat_id)
        )
        conn.commit()
        return cursor.rowcount > 0


async def run_reminder_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und prüft jede Minute auf fällige Erinnerungen."""
    _init_db()
    logger.info("Reminder Scheduler gestartet.")
    while True:
        try:
            pending = get_pending_reminders()
            for reminder in pending:
                try:
                    await bot.send_message(
                        chat_id=reminder["chat_id"],
                        text=f"⏰ *Erinnerung:* {reminder['text']}",
                        parse_mode="Markdown",
                    )
                    mark_sent(reminder["id"])
                    logger.info(f"Erinnerung gesendet: id={reminder['id']} text={reminder['text'][:50]}")
                except Exception as e:
                    logger.error(f"Erinnerung senden fehlgeschlagen: {e}")
        except Exception as e:
            logger.error(f"Reminder Scheduler Fehler: {e}")
        await asyncio.sleep(60)
