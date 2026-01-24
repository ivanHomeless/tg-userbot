import asyncio
import time
import logging
from pathlib import Path

from telethon.errors import FloodWaitError
from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.config import API_ID, API_HASH, PHONE, SOURCES_LINKS, SOURCES_IDS, DEST, TEMP_DIR, POST_DELAY, SESSION_NAME
from app.database import db_init, is_seen, mark_seen
from app.utils import split_text, save_source_id, remove_link_from_file
from app import ai

logger = logging.getLogger(__name__)

ALBUM_SILENCE_TIMEOUT = 3.0



class TGBot:
    def __init__(self):
        self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH, sequential_updates=True)
        self.post_lock = asyncio.Lock()
        self.last_post_time = 0.0
        self.albums = {}

    async def setup(self):
        db_init()
        Path(TEMP_DIR).mkdir(exist_ok=True)
        logger.info("✅ База и папки готовы.")

    async def join_sources(self):
        """Универсальное вступление в каналы"""
        logger.info(f"🔄 Проверка подписок ({len(SOURCES_IDS)} источников)...")
        for src in SOURCES_LINKS:
            try:
                src = src.strip()
                entity = None

                if '+' in src or 'joinchat' in src:
                    invite_hash = src.split('/')[-1].replace('+', '')
                    try:
                        updates = await self.client(ImportChatInviteRequest(invite_hash))
                        if updates.chats:
                            entity = updates.chats[0]
                            logger.info(f"✅ Вступил в приватный: {entity.title} (ID: {entity.id})")
                    except UserAlreadyParticipantError:
                        entity = await self.client.get_entity(src)
                        logger.info(f"ℹ️ Уже в чате: {entity.title} (ID: {entity.id})")
                else:
                    entity = await self.client.get_entity(src)
                    await self.client(JoinChannelRequest(entity))
                    logger.info(f"✅ Подписан на: {src}")

                if entity:
                    save_source_id(entity.id)
                    remove_link_from_file(src)
                await asyncio.sleep(2)
            except FloodWaitError as e:
                logger.warning(f"⏳ Слишком много запросов! Ждем {e.seconds} сек...")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                remove_link_from_file(src)
                logger.error(f"⚠️ Ошибка {src}: {e}")

    async def _wait_smart_delay(self):
        """Умная задержка между постами"""
        now = time.time()
        wait = (self.last_post_time + POST_DELAY) - now
        if wait > 0:
            logger.info(f"⏳ Пауза перед постом: {int(wait)}с")
            await asyncio.sleep(wait)

    async def _send_to_dest(self, paths, text):
        """Унифицированная отправка медиа и текста"""
        try:
            if paths:
                if len(text) > 1024:
                    # Отправляем медиа и проверяем результат
                    media_result = await self.client.send_file(DEST, paths)

                    # Универсальная проверка (для одного файла и для альбома)
                    messages = media_result if isinstance(media_result, list) else [media_result]
                    uploaded = [msg for msg in messages if msg and msg.media]

                    logger.info(f"✅ Медиа загружено: {len(uploaded)}/{len(messages)} файлов")

                    # Отправляем текст только если медиа загрузилось
                    if uploaded and text:
                        # Минимальная задержка для гарантии обработки сервером
                        await asyncio.sleep(10)

                        chunks = list(split_text(text))
                        logger.info(f"📝 Отправка текста: {len(chunks)} чанк(ов)")

                        for chunk in chunks:
                            await self.client.send_message(DEST, chunk)
                            await asyncio.sleep(0.3)
                    elif not uploaded:
                        logger.error("❌ Медиа не загрузилось, текст не отправлен")
                else:
                    # Медиа с caption (текст <= 1024)
                    await self.client.send_file(DEST, paths, caption=text or None)
                    logger.info(f"✅ Медиа+caption отправлено")
            elif text:
                # Только текст без медиа
                chunks = list(split_text(text))
                logger.info(f"📝 Отправка только текста: {len(chunks)} чанк(ов)")
                for chunk in chunks:
                    await self.client.send_message(DEST, chunk)
                    await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке в DEST: {e}", exc_info=True)
            raise  # Пробрасываем ошибку выше

    async def send_album(self, gid):
        """Сборка и отправка медиа-группы"""
        try:
            await asyncio.sleep(ALBUM_SILENCE_TIMEOUT)
        except asyncio.CancelledError:
            logger.debug(f"Альбом {gid}: таймер отменен")
            return

        data = self.albums.pop(gid, None)
        if not data:
            logger.warning(f"Альбом {gid}: данные не найдены")
            return

        # Дожидаемся загрузки всех файлов
        paths = await asyncio.gather(*data['tasks'], return_exceptions=True)

        # Фильтруем валидные пути и логируем ошибки
        valid_paths = []
        for i, p in enumerate(paths, 1):
            if isinstance(p, Exception):
                logger.error(f"❌ Альбом {gid}: ошибка загрузки файла {i}: {p}")
            elif isinstance(p, str) and p and Path(p).exists():
                valid_paths.append(p)
            elif p:
                logger.warning(f"⚠️ Альбом {gid}: файл {i} не существует: {p}")

        # Проверяем что есть хоть что-то
        if not valid_paths:
            logger.warning(f"⚠️ Альбом {gid}: нет валидных файлов, пропускаем")
            return

        logger.info(f"📦 Альбом {gid}: {len(valid_paths)}/{len(paths)} файлов готовы")

        full_text = "\n".join([t for t in data['texts'] if t]).strip()
        rewritten = ai.rewrite_text(full_text) if full_text else ""

        async with self.post_lock:
            await self._wait_smart_delay()
            try:
                await self._send_to_dest(valid_paths, rewritten)
                self.last_post_time = time.time()
            except Exception as e:
                logger.error(f"❌ Альбом {gid}: ошибка при отправке: {e}")

        # Безопасное удаление файлов
        for p in valid_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"❌ Не удалось удалить {p}: {e}")

    async def process_message(self, event):
        """Обработка входящих сообщений"""
        chat_id = event.chat_id

        # Быстрая проверка по нормализованным ID
        if chat_id not in SOURCES_IDS:
            return

        if is_seen(chat_id, event.id):
            return
        mark_seen(chat_id, event.id)

        msg = event.message
        gid = msg.grouped_id
        text = (msg.message or "").strip()

        # Запускаем загрузку медиа асинхронно
        dl_task = asyncio.create_task(self.client.download_media(msg, file=TEMP_DIR))

        if gid:
            # Альбом (Debounce)
            if gid not in self.albums:
                self.albums[gid] = {'tasks': [], 'texts': [], 'timer_task': None}

            self.albums[gid]['tasks'].append(dl_task)
            if text:
                self.albums[gid]['texts'].append(text)

            # Отменяем предыдущий таймер
            if self.albums[gid]['timer_task']:
                self.albums[gid]['timer_task'].cancel()

            # Запускаем новый таймер
            self.albums[gid]['timer_task'] = asyncio.create_task(self.send_album(gid))
        else:
            # Одиночное сообщение
            try:
                path = await dl_task
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки медиа: {e}")
                path = None

            rewritten = ai.rewrite_text(text) if text else ""

            async with self.post_lock:
                await self._wait_smart_delay()
                try:
                    paths = [path] if path and Path(path).exists() else []
                    await self._send_to_dest(paths, rewritten)
                    self.last_post_time = time.time()
                except Exception as e:
                    logger.error(f"❌ Ошибка одиночного поста: {e}")
                finally:
                    # Удаляем файл в любом случае
                    if path:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except Exception as e:
                            logger.error(f"❌ Не удалось удалить {path}: {e}")

    async def run(self):
        await self.client.start(phone=PHONE)
        await self.setup()
        await self.join_sources()
        self.client.add_event_handler(self.process_message, events.NewMessage())
        print("🚀 Бот запущен и слушает каналы...")
        await self.client.run_until_disconnected()