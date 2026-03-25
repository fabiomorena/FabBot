import logging
import os
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
load_dotenv()

# Log-Verzeichnis
LOG_DIR = Path.home() / ".fabbot"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "fabbot.log"

# Handler: Datei mit täglicher Rotation (7 Tage)
file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

# Handler: Terminal (wie bisher)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

def main() -> None:
    from bot.bot import build_bot
    logger.info("Mac Agent startet...")
    logger.info(f"Logs werden geschrieben nach: {LOG_FILE}")
    app = build_bot()
    logger.info("Telegram Bot läuft. Ctrl+C zum Beenden.")
    app.run_polling()

if __name__ == "__main__":
    main()
