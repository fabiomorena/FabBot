import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    from bot.bot import build_bot
    logger.info("Mac Agent startet...")
    app = build_bot()
    logger.info("Telegram Bot läuft. Ctrl+C zum Beenden.")
    app.run_polling()


if __name__ == "__main__":
    main()