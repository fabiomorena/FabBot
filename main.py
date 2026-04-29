import atexit
import logging
import os
import subprocess
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
load_dotenv()

LOG_DIR = Path.home() / ".fabbot"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "fabbot.log"
PID_FILE = LOG_DIR / "bot.pid"

file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[file_handler])

# Telegram- und httpx-interne Logger dämpfen – reduziert Rauschen im Log
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


class _ConflictFilter(logging.Filter):
    """Unterdrückt Conflict-Tracebacks aus dem Telegram-networkloop."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "Conflict" in msg or "terminated by other getUpdates" in msg:
            return False
        exc = record.exc_info
        if exc and exc[1] is not None and "Conflict" in type(exc[1]).__name__:
            return False
        return True


_conflict_filter = _ConflictFilter()
logging.getLogger("telegram.ext.Application").addFilter(_conflict_filter)
logging.getLogger("telegram.ext._utils.networkloop").addFilter(_conflict_filter)
logger = logging.getLogger(__name__)


def _check_single_instance() -> None:
    import fcntl
    lock_path = LOG_DIR / "bot.lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Bot läuft bereits (Lock aktiv). Abbruch.")
        sys.exit(0)
    my_pid = str(os.getpid())
    PID_FILE.write_text(my_pid)
    def _cleanup():
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == my_pid:
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass
    atexit.register(_cleanup)


def main() -> None:
    _check_single_instance()

    # Verhindert Mac-Sleep solange der Bot läuft (ersetzt caffeinate als plist-Parent).
    _caff = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    atexit.register(_caff.terminate)

    # Phase 70: TTS-Konfiguration validieren NACHDEM Logger konfiguriert ist
    # (Lazy Import nötig – logging.basicConfig() muss vor dem tts-Import aktiv sein)
    from bot.tts import validate_tts_config
    validate_tts_config()

    from bot.bot import build_bot
    logger.info("Mac Agent startet...")
    logger.info(f"Logs werden geschrieben nach: {LOG_FILE}")
    app = build_bot()
    logger.info("Telegram Bot läuft. Ctrl+C zum Beenden.")
    # Phase 103 (Issue #16): drop_pending_updates=True verhindert Doppel-Antworten
    # nach Bot-Neustart. Telegram puffert unbestätigte Updates – ohne diesen Flag
    # werden sie beim nächsten Start erneut verarbeitet (je Neustart eine Antwort).
    # Da FabBot ein persönlicher Bot ist, sind während eines Crashes verpasste
    # Messages irrelevant – sauberer Start ist wichtiger als Vollständigkeit.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
