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
logger = logging.getLogger(__name__)


def _check_single_instance() -> None:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            print(f"Bot läuft bereits (PID {old_pid}). Abbruch.")
            sys.exit(1)
        except (ProcessLookupError, PermissionError):
            pass  # Stale PID-File
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))


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
