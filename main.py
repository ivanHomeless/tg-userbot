import asyncio
import logging
import sys
from pathlib import Path
from bot_logic import TGBot


def setup_logging():
    # Создаем папку для логов, если её нет
    Path("logs").mkdir(exist_ok=True)

    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler("logs/bot_work.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger('telethon').setLevel(logging.WARNING)


async def main():
    Path("data").mkdir(exist_ok=True)
    Path("tmp_media").mkdir(exist_ok=True)
    # ------------------------------------------------

    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Инициализация бота...")

    try:
        bot = TGBot()
        await bot.run()
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Бот остановлен вручную.")