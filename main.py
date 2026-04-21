import logging
import os
import subprocess
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
load_dotenv()

LOG_DIR = Path.home() / ".fabbot"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "fabbot.log"

file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)


def main() -> None:
    # Verhindert Mac-Sleep solange der Bot läuft (ersetzt caffeinate als plist-Parent).
    _caff = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])

    # Phase 70: TTS-Konfiguration validieren NACHDEM Logger konfiguriert ist
    from bot.tts import _validate_tts_config
    _validate_tts_config()

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
