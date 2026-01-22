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
        # 1. Ждем, пока Telegram дошлет все части альбома
        await asyncio.sleep(10)

        async with self.post_lock:
            # Получаем данные и СРАЗУ удаляем их из кэша, чтобы другие процессы не мешали
            tasks = self.album_cache.pop(gid, [])
            raw_text = self.album_text.pop(gid, "")

            if not tasks:
                return

            logger.info(f"📦 Начинаю сборку альбома {gid} ({len(tasks)} файлов)")

            # 2. Соблюдаем общую задержку между постами
            wait = (self.last_post_time + POST_DELAY) - time.time()
            if wait > 0:
                logger.info(f"⏳ Очередь: ждем {int(wait)} сек...")
                await asyncio.sleep(wait)

            # 3. Дожидаемся скачивания всех файлов
            paths = await asyncio.gather(*tasks, return_exceptions=True)
            valid_paths = [p for p in paths if isinstance(p, str) and Path(p).exists()]

            # 4. Рерайт
            rewritten = ai.rewrite_text(raw_text) if raw_text else ""

            # 5. Отправка
            try:
                if valid_paths:
                    if len(rewritten) <= 1024:
                        await self.client.send_file(DEST, valid_paths, caption=rewritten)
                    else:
                        await self.client.send_file(DEST, valid_paths)
                        await asyncio.sleep(2)
                        await self.client.send_message(DEST, rewritten)
                elif rewritten:
                    # Если файлы не скачались, но есть текст - шлем хотя бы текст
                    await self.client.send_message(DEST, rewritten)

                self.last_post_time = time.time()
                logger.info(f"✅ Альбом {gid} успешно отправлен")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки альбома {gid}: {e}")
            finally:
                # Очистка файлов
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
        if not (event.is_channel or event.is_group):
            return

        # Проверка источника
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        src_id = f"@{username}".lower() if username else str(event.chat_id)

        if not any(s.strip().lower() in src_id for s in SOURCES):
            return

        # Анти-дубль
        if is_seen(event.chat_id, event.id):
            return
        mark_seen(event.chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id

        if gid:
            # Важно: создаем корутину скачивания, но не ждем её здесь!
            coro = self.client.download_media(msg, file=TEMP_DIR)

            if gid not in self.album_cache:
                self.album_cache[gid] = [coro]
                self.album_text[gid] = (msg.message or "").strip()
                # Запускаем фоновую задачу на сборку
                asyncio.create_task(self.post_album(gid))
            else:
                self.album_cache[gid].append(coro)
                if not self.album_text[gid] and msg.message:
                    self.album_text[gid] = msg.message.strip()
        else:
            # Одиночный пост
            path = await self.client.download_media(msg, file=TEMP_DIR) if msg.media else None
            text = (msg.message or "").strip()
            rewritten = ai.rewrite_text(text) if text else ""
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

        print("Бот успешно запущен. Нажмите Ctrl+C для остановки.")
        await self.client.run_until_disconnected()