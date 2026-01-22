import asyncio
import logging
import sys
from bot_logic import TGBot


# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
def setup_logging():
    # Создаем форматтер: Время - Уровень - Сообщение
    log_format = '%(asctime)s - %(levelname)s - %(message)s'

    # Базовая конфигурация
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            # 1. Запись в файл (UTF-8 важна для русского языка в рерайтах)
            logging.FileHandler("bot_work.log", encoding="utf-8"),
            # 2. Вывод в консоль (если нужно совсем убрать — удали строку ниже)
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Убираем лишний "шум" от самой библиотеки Telethon (ставим ей уровень WARNING)
    logging.getLogger('telethon').setLevel(logging.WARNING)


async def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Инициализация бота...")

    bot = TGBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Бот остановлен вручную.")