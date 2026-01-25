import asyncio
import logging
from app.bot_logic import TGBot

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('logs/bot_work.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
    logger.info("=" * 60)
    logger.info("🚀 Запуск Telegram бота с PostgreSQL")
    logger.info("=" * 60)
    
    bot = TGBot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("\n👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("🛑 Завершение работы бота")


if __name__ == "__main__":
    asyncio.run(main())
