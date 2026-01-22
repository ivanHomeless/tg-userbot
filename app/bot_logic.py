import asyncio
import time
import logging
from pathlib import Path
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest

# Импортируем настройки и вспомогательные функции
from app.config import API_ID, API_HASH, PHONE, SOURCES, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app import ai

logger = logging.getLogger(__name__)


class TGBot:
    def __init__(self):
        # Инициализируем клиент без await (в конструкторе это запрещено)
        # Используем путь data/userbot_session
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        self.post_lock = asyncio.Lock()
        self.last_post_time = 0.0
        self.album_cache = {}
        self.album_text = {}

    async def setup(self):
        """Инициализация базы и AI клиента"""
        db_init()
        Path(TEMP_DIR).mkdir(exist_ok=True)
        logger.info("Компоненты (DB, AI, MediaDir) готовы.")

    async def join_sources(self):
        """Автоматическая подписка на источники"""
        logger.info(f"Проверка подписок для {len(SOURCES)} источников...")
        for src in SOURCES:
            try:
                # Вызываем метод через клиент
                await self.client(JoinChannelRequest(src))
                logger.info(f"Подписка на {src} проверена/выполнена.")
            except Exception as e:
                logger.debug(f"Инфо по подписке {src}: {e}")

    async def post_album(self, gid):
        await asyncio.sleep(4)
        async with self.post_lock:
            tasks = self.album_cache.get(gid, [])
            raw_text = self.album_text.get(gid, "")
            if not tasks: return

            paths = await asyncio.gather(*tasks)
            valid_paths = [p for p in paths if p]

            # Рерайт делаем один раз здесь
            rewritten = ai.rewrite_text(raw_text) if raw_text else ""

            try:
                if valid_paths:
                    if len(rewritten) <= 1024:
                        # Вариант А: Текст влезает в подпись
                        await self.client.send_file(DEST, valid_paths, caption=rewritten)
                    else:
                        # Вариант Б: Текст слишком длинный — шлем раздельно
                        await self.client.send_file(DEST, valid_paths)  # Сначала медиа
                        await asyncio.sleep(1.5)  # Пауза, чтобы Telegram не перепутал порядок
                        await self.client.send_message(DEST, rewritten)  # Потом текст

                    logger.info(f"✅ Альбом {gid} отправлен.")
            except Exception as e:
                logger.error(f"Ошибка при отправке альбома: {e}")
            finally:
                # Чистим кэш и файлы ВСЕГДА
                self.album_cache.pop(gid, None)
                self.album_text.pop(gid, None)
                for p in valid_paths:
                    Path(p).unlink(missing_ok=True)

    async def safe_post(self, text, file_path=None):
        """Отправка одиночного сообщения с задержкой"""
        async with self.post_lock:
            wait = (self.last_post_time + POST_DELAY) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                if file_path:
                    if len(text) <= 1024:
                        await self.client.send_file(DEST, file_path, caption=text)
                    else:
                        await self.client.send_file(DEST, file_path)
                        await asyncio.sleep(1)
                        await self.client.send_message(DEST, text)
                else:
                    await self.client.send_message(DEST, text)

                self.last_post_time = time.time()
                logger.info("✅ Одиночный пост отправлен.")
            except Exception as e:
                logger.error(f"Ошибка safe_post: {e}")
            finally:
                if file_path:
                    Path(file_path).unlink(missing_ok=True)

    async def process_message(self, event):
        """Обработка входящих событий"""
        if not (event.is_channel or event.is_group):
            return

        # Получаем данные о чате
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        src_id = f"@{username}".lower() if username else str(event.chat_id)

        # Проверка источника
        clean_sources = [s.strip().lower() for s in SOURCES]
        if not any(s in src_id for s in clean_sources):
            return

        # Анти-дубль
        if is_seen(event.chat_id, event.id):
            return
        mark_seen(event.chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id

        if gid:
            # Логика альбомов
            if gid not in self.album_cache:
                self.album_cache[gid] = [self.client.download_media(msg, file=TEMP_DIR)]
                self.album_text[gid] = (msg.message or "").strip()
                # Создаем задачу на отправку через 4 секунды
                asyncio.create_task(self.post_album(gid))
            else:
                # Добавляем задачу скачивания в список
                self.album_cache[gid].append(self.client.download_media(msg, file=TEMP_DIR))
        else:
            # Логика одиночных сообщений
            text = (msg.message or "").strip()
            rewritten = ai.rewrite_text(text) if text else ""

            path = await self.client.download_media(msg, file=TEMP_DIR) if msg.media else None
            await self.safe_post(rewritten, path)

    async def run(self):
        """Основной цикл запуска"""
        logger.info("Запуск Telegram сессии...")
        # Метод .start() сам управляет подключением и авторизацией
        await self.client.start(phone=PHONE)

        await self.setup()
        await self.join_sources()

        # Регистрация обработчика
        self.client.add_event_handler(self.process_message, events.NewMessage)

        logger.info("Бот успешно запущен. Нажмите Ctrl+C для остановки.")
        await self.client.run_until_disconnected()