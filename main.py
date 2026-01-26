import asyncio
import logging
import signal
import sys
from pathlib import Path
from app.bot_logic import TGBot

# Создаём папку для логов
Path("logs").mkdir(exist_ok=True)

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

# Флаг для graceful shutdown
shutdown_event = asyncio.Event()


def signal_handler(sig, frame):
    """Обработчик сигналов (Ctrl+C, SIGTERM)"""
    signal_name = signal.Signals(sig).name
    logger.info(f"\n⚠️  Получен сигнал {signal_name}, завершаем работу...")
    shutdown_event.set()


async def main():
    """Главная функция запуска бота"""
    logger.info("=" * 60)
    logger.info("🚀 Запуск Telegram бота с PostgreSQL")
    logger.info("=" * 60)
    
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill
    
    bot = TGBot()
    
    try:
        # Запускаем бота в отдельной задаче
        bot_task = asyncio.create_task(bot.run())
        
        # Ждём либо завершения бота, либо сигнала остановки
        await asyncio.wait(
            [bot_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        if shutdown_event.is_set():
            logger.info("👋 Останавливаем бота...")
            
            # Отключаем Telethon клиента
            if bot.client.is_connected():
                await bot.client.disconnect()
            
            # Отменяем задачу бота
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
            
            logger.info("✅ Бот корректно остановлен")
        
    except KeyboardInterrupt:
        # На случай если signal_handler не сработал
        logger.info("\n👋 Бот остановлен пользователем (Ctrl+C)")
    
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
    
    finally:
        logger.info("🛑 Завершение работы бота")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Последняя линия защиты
        print("\n👋 Бот остановлен")
        sys.exit(0)
