import shutil
import asyncio
import logging
import sys
from pathlib import Path

# Импортируем класс бота из папки app
from app.bot_logic import TGBot


def init_environment():
    """Создаем структуру папок перед запуском"""
    for folder in ["data", "logs"]:
        Path(folder).mkdir(exist_ok=True)
        # Особый подход к папке с медиа

    temp_path = Path("tmp_media")
    if temp_path.exists():
        # Удаляем папку со всем содержимым и создаем пустую
        shutil.rmtree(temp_path)

    temp_path.mkdir(exist_ok=True)
    logging.info("Временные файлы очищены, структура папок готова.")


def setup_logging():
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        handlers=[
            logging.FileHandler("logs/bot_work.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ],

    )
    # Гасим лишние логи библиотек
    #logging.getLogger('telethon').setLevel(logging.WARNING)
    #logging.getLogger('openai').setLevel(logging.WARNING)
    #logging.getLogger('httpx').setLevel(logging.WARNING)


async def main():
    init_environment()
    setup_logging()

    logger = logging.getLogger("main")
    logger.info("Запуск приложения из папки app...")
    try:
        bot = TGBot()
        await bot.run()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Работа бота завершена пользователем.")